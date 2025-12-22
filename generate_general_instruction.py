import argparse
import os
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from img_parse import get_creds, giga_free_answer, get_token_stats


PAGE_HEADER_RE = re.compile(r"^##\s*Страница\s+(\d+)\s*$", re.MULTILINE)


def _split_by_page_headers(md: str) -> List[Tuple[int, str]]:
    """
    Парсинг формата instructions_merged.md:
      ## Страница 001
      ...текст...
      ## Страница 002
    """
    matches = list(PAGE_HEADER_RE.finditer(md))
    if not matches:
        return []

    result: List[Tuple[int, str]] = []
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        content = md[start:end].strip()
        result.append((page_num, content))
    return result


def _load_pages_from_doc_dir(doc_dir: Path) -> List[Tuple[int, str]]:
    """
    Читает out/<doc>/page_XXX/instruction.txt.
    """
    page_dirs = sorted([p for p in doc_dir.iterdir() if p.is_dir() and p.name.startswith("page_")])
    pages: List[Tuple[int, str]] = []
    for pd in page_dirs:
        try:
            page_num = int(pd.name.split("_", 1)[-1])
        except ValueError:
            continue
        ip = pd / "instruction.txt"
        if not ip.exists():
            continue
        txt = ip.read_text(encoding="utf-8").strip()
        if not txt:
            continue
        pages.append((page_num, txt))
    return pages


def _batch_pages(pages: List[Tuple[int, str]], max_chars: int) -> List[List[Tuple[int, str]]]:
    """
    Грубая упаковка страниц по лимиту символов (для устойчивости к размеру контекста).
    """
    batches: List[List[Tuple[int, str]]] = []
    cur: List[Tuple[int, str]] = []
    cur_len = 0
    for page_num, text in pages:
        piece = f"\n\n=== PAGE {page_num:03d} ===\n{text}\n"
        if cur and (cur_len + len(piece) > max_chars):
            batches.append(cur)
            cur = []
            cur_len = 0
        cur.append((page_num, text))
        cur_len += len(piece)
    if cur:
        batches.append(cur)
    return batches


def _render_batch_text(batch: List[Tuple[int, str]]) -> str:
    chunks: List[str] = []
    for page_num, text in batch:
        chunks.append(f"=== PAGE {page_num:03d} ===\n{text}".strip())
    return "\n\n".join(chunks).strip()


def _generate_general_instruction_from_pages(
    pages: List[Tuple[int, str]],
    access_token: str | None,
    *,
    batch_max_chars: int = 12000,
    output_tokens: int = 12000,
) -> str:
    """
    1) Генерируем "черновики" по батчам страниц (без домыслов)
    2) Сливаем черновики в единый документ (без домыслов)
    """
    if not pages:
        return ""

    sys_prompt = (
        "Ты — старший методолог и сотрудник кредитного отдела банка. "
        "Ты пишешь ЕДИНУЮ общую инструкцию по работе в АС.\n"
        "Критично:\n"
        "- НЕЛЬЗЯ придумывать функционал, кнопки, экраны, шаги, статусы, поля, причины, сроки, ограничения, "
        "если этого нет во входных инструкциях.\n"
        "- Можно убирать повторы, упорядочивать и нормализовать формулировки, НЕ расширяя смысл.\n"
        "- Если во входе встречаются моковые/примерные значения (ФИО, номера, суммы, даты и т.п.), "
        "не копируй их дословно: замени на <значение>/<пример>.\n"
        "- Пиши в markdown, с короткими заголовками и нумерованными шагами там, где уместно.\n"
        "- Не упоминай номера страниц, слово 'PAGE', и не ссылайся на источники.\n"
    )

    # Pass 1: батчи
    drafts: List[str] = []
    for batch in _batch_pages(pages, max_chars=batch_max_chars):
        batch_text = _render_batch_text(batch)
        question = (
            "Ниже дан набор инструкций по разным страницам одного документа.\n"
            "Собери из них связный фрагмент общей инструкции.\n"
            "Важно: в ответе должно быть ТОЛЬКО то, что прямо следует из текста.\n"
            "Если для какого-то действия не хватает информации — не добавляй её.\n\n"
            "ВХОД:\n"
            "----------------------------------------\n"
            f"{batch_text}\n"
            "----------------------------------------\n\n"
            "ВЫВОД: верни только markdown-текст фрагмента инструкции, без пояснений."
        )
        drafts.append(
            giga_free_answer(
                question=question,
                access_token=access_token,
                sys_prompt=sys_prompt,
                max_tokens=output_tokens,
            ).strip()
        )

    # Pass 2: финальная склейка
    merged_input = "\n\n".join(
        [f"=== DRAFT {i + 1:02d} ===\n{d}".strip() for i, d in enumerate(drafts) if d.strip()]
    ).strip()
    if not merged_input:
        return ""

    merge_question = (
        "Ниже несколько фрагментов общей инструкции (drafts), полученных из разных страниц.\n"
        "Собери из них один итоговый документ.\n\n"
        "Строгие правила:\n"
        "- НЕ добавляй новых шагов/условий/терминов, которых нет в drafts.\n"
        "- Убери повторы и противоречия: если есть конфликт, оставь оба варианта как альтернативы "
        "с нейтральной формулировкой (не выбирай сам).\n"
        "- Оставь только markdown итоговой инструкции.\n\n"
        "DRAFTS:\n"
        "----------------------------------------\n"
        f"{merged_input}\n"
        "----------------------------------------\n"
    )
    return giga_free_answer(
        question=merge_question,
        access_token=access_token,
        sys_prompt=sys_prompt,
        max_tokens=output_tokens,
    ).strip() + "\n"


def _render_docx_from_markdown_file(md_path: Path, docx_path: Path, *, title: str = "Instruction") -> None:
    """
    Конвертация Markdown → DOCX через pandoc (CLI).
    Требует установленный pandoc в окружении (или указать путь через PANDOC_PATH).
    """
    pandoc = (os.getenv("PANDOC_PATH") or "pandoc").strip()
    reference_docx = (os.getenv("PANDOC_REFERENCE_DOCX") or "").strip()

    cmd = [
        pandoc,
        str(md_path),
        "-f",
        "markdown",
        "-t",
        "docx",
        "-o",
        str(docx_path),
        "--metadata",
        f"title={title}",
    ]
    if reference_docx:
        cmd.extend(["--reference-doc", reference_docx])

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise RuntimeError("Pandoc не найден. Установите pandoc или задайте путь через PANDOC_PATH.") from e
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "") + "\n" + (e.stderr or "")
        raise RuntimeError(f"Ошибка pandoc при конвертации в docx:\n{out.strip()}") from e


def merge_instructions_to_docx(doc_dir: str | Path, output_md: str | Path | None = None, output_docx: str | Path | None = None) -> Tuple[Path, Path]:
    """
    Склеивает все instruction.txt из page_XXX директорий документа в один markdown файл,
    а затем конвертирует его в docx через pandoc.

    Args:
        doc_dir: Путь к каталогу документа (например, out/web/<uuid>/)
        output_md: Путь для сохранения объединённого markdown. По умолчанию: <doc_dir>/instructions_merged.md
        output_docx: Путь для сохранения docx. По умолчанию: <doc_dir>/instructions_merged.docx

    Returns:
        Tuple[Path, Path]: (путь к md файлу, путь к docx файлу)
    """
    doc_dir = Path(doc_dir)
    if not doc_dir.exists():
        raise FileNotFoundError(f"Каталог документа не найден: {doc_dir}")

    # Загружаем страницы
    pages = _load_pages_from_doc_dir(doc_dir)
    if not pages:
        raise ValueError(f"Не найдено инструкций в {doc_dir}. Ожидаются page_XXX/instruction.txt")

    pages = sorted(pages, key=lambda x: x[0])

    # Формируем markdown с заголовками страниц
    md_parts: List[str] = []
    for page_num, content in pages:
        md_parts.append(f"## Страница {page_num:03d}\n\n{content}")

    merged_md = "\n\n".join(md_parts) + "\n"

    # Определяем пути вывода
    md_path = Path(output_md) if output_md else (doc_dir / "instructions_merged.md")
    docx_path = Path(output_docx) if output_docx else (doc_dir / "instructions_merged.docx")

    # Сохраняем markdown
    md_path.write_text(merged_md, encoding="utf-8")
    print(f"Объединённый markdown сохранён: {md_path}")

    # Конвертируем в docx через pandoc
    title = doc_dir.name if doc_dir.name else "Instructions"
    _render_docx_from_markdown_file(md_path, docx_path, title=title)
    print(f"DOCX сохранён: {docx_path}")

    return md_path, docx_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Генерация общей инструкции (general_instruction.md) из 'простых' инструкций.\n"
            "Источник данных:\n"
            "- либо каталог документа out/<doc>/ с page_XXX/instruction.txt,\n"
            "- либо файл out/<doc>/instructions_merged.md (с заголовками '## Страница NNN').\n"
            "FAQ не используется."
        )
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Путь к каталогу документа (out/<doc>/) или к instructions_merged.md.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Куда сохранить markdown. По умолчанию рядом: <doc>/general_instruction.md",
    )
    parser.add_argument(
        "--docx",
        action="store_true",
        help="Дополнительно сгенерировать DOCX (через pandoc). По умолчанию: рядом с .md.",
    )
    parser.add_argument(
        "--out-docx",
        type=str,
        default="",
        help="Куда сохранить docx (если указан --docx). По умолчанию: <doc>/general_instruction.docx",
    )
    parser.add_argument(
        "--batch-max-chars",
        type=int,
        default=12000,
        help="Максимум символов на один батч страниц. По умолчанию 12000.",
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=12000,
        help="Лимит max_tokens для одного ответа модели. По умолчанию 12000.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Только склеить instruction.txt без обработки LLM. Сохраняет instructions_merged.md и .docx.",
    )
    parser.add_argument(
        "--out-merged-md",
        type=str,
        default="",
        help="Путь для объединённого markdown (при --merge-only). По умолчанию: <doc>/instructions_merged.md",
    )

    args = parser.parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        raise FileNotFoundError(f"Не найдено: {in_path}")

    # Режим простой склейки (без LLM)
    if args.merge_only:
        if not in_path.is_dir():
            raise ValueError("Для --merge-only нужен каталог документа, а не файл.")
        out_md = Path(args.out_merged_md) if args.out_merged_md else None
        out_docx = Path(args.out_docx) if args.out_docx else None
        merge_instructions_to_docx(in_path, output_md=out_md, output_docx=out_docx)
        return

    # Авторизация
    creds = get_creds()
    access_token = creds.get("access_token")
    if not access_token and creds.get("auth_mode") != "cert":
        raise RuntimeError(f"Токен не получен от NGW. Ответ: {creds}")

    # Источник страниц
    pages: List[Tuple[int, str]] = []
    doc_dir: Path
    if in_path.is_dir():
        doc_dir = in_path
        pages = _load_pages_from_doc_dir(doc_dir)
    else:
        doc_dir = in_path.parent
        md_text = in_path.read_text(encoding="utf-8")
        pages = _split_by_page_headers(md_text)

    if not pages:
        raise ValueError(
            "Не удалось извлечь инструкции. "
            "Ожидаю либо каталог с page_XXX/instruction.txt, либо markdown с заголовками '## Страница NNN'."
        )

    pages = sorted(pages, key=lambda x: x[0])

    out_path = Path(args.out) if args.out else (doc_dir / "general_instruction.md")
    md_text = _generate_general_instruction_from_pages(
        pages=pages,
        access_token=access_token,
        batch_max_chars=int(args.batch_max_chars),
        output_tokens=int(args.output_tokens),
    )
    out_path.write_text(md_text, encoding="utf-8")
    print(f"Общая инструкция сохранена: {out_path}")

    if args.docx:
        out_docx = Path(args.out_docx) if args.out_docx else out_path.with_suffix(".docx")
        title = doc_dir.name if doc_dir.name else "Instruction"
        _render_docx_from_markdown_file(out_path, out_docx, title=title)
        print(f"DOCX сохранён: {out_docx}")

    stats = get_token_stats()
    print(
        "\nИТОГО по токенам в этом запуске (general_instruction):\n"
        f"- prompt_tokens     = {stats.get('prompt_tokens', 0)}\n"
        f"- completion_tokens = {stats.get('completion_tokens', 0)}\n"
        f"- total_tokens      = {stats.get('total_tokens', 0)}"
    )


if __name__ == "__main__":
    main()


