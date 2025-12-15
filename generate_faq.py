import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple

from img_parse import get_creds, giga_free_answer, get_token_stats
from openpyxl import Workbook


PAGE_HEADER_RE = re.compile(r"^##\s*Страница\s+(\d+)\s*$", re.MULTILINE)
SOURCE_TAG_RE = re.compile(r"\[SOURCE:\s*page\s*(\d{1,3})\s*\]", re.IGNORECASE)
FAQ_BLOCK_RE = re.compile(
    r"ВОПРОС:\s*(?P<q>.*?)(?:\r?\n)+"
    r"(?:ОТВЕТ|ИНСТРУКЦИЯ):\s*(?P<a>.*?)(?:\r?\n)+"
    r"\[SOURCE\s*-\s*\"(?P<s>.*?)\"\]\s*",
    re.DOTALL | re.IGNORECASE,
)


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


def _group_lines_by_source_tags(md: str) -> Dict[int, List[str]]:
    """
    Парсинг формата instructions_incremental.md:
    каждая смысловая строка содержит [SOURCE: page XXX]
    """
    groups: Dict[int, List[str]] = {}
    for raw_line in md.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = SOURCE_TAG_RE.search(line)
        if not m:
            continue
        page_num = int(m.group(1))
        groups.setdefault(page_num, []).append(line)
    return groups


def _build_doc_context(md: str, max_chars: int = 8000) -> str:
    """
    Ограничиваем контекст документа, чтобы не раздувать промпт.
    """
    md = md.strip()
    if len(md) <= max_chars:
        return md
    return md[:max_chars] + "\n\n[...ОБРЕЗАНО...]\n"


def _parse_faq_blocks(text: str) -> List[Dict[str, str]]:
    """
    Парсим ответы модели в блоках:
      ВОПРОС: ...
      ОТВЕТ/ИНСТРУКЦИЯ: ...
      [SOURCE - "..."]
    """
    items: List[Dict[str, str]] = []
    for m in FAQ_BLOCK_RE.finditer(text.strip()):
        q = m.group("q").strip()
        a = m.group("a").strip()
        s = m.group("s").strip()
        if q and a and s:
            items.append({"question": q, "answer": a, "source": s})
    return items


def _rows_to_xlsx(rows: List[Dict[str, str]], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "FAQ"
    ws.append(["Вопрос", "Ответ", "Источник"])
    for r in rows:
        ws.append([r.get("question", ""), r.get("answer", ""), r.get("source", "")])

    # Немного авто-ширины (ограниченно)
    for col, width in (("A", 60), ("B", 90), ("C", 40)):
        ws.column_dimensions[col].width = width

    wb.save(out_path)


def generate_faq_for_pages(
    pages: List[Tuple[int, str]],
    full_doc_context: str,
    access_token: str,
    pamphlet_name: str,
    output_tokens: int = 10000,
) -> str:
    """
    Для каждой страницы генерируем 3–5 пар ВОПРОС/ИНСТРУКЦИЯ (FAQ).
    Возвращаем markdown.
    """
    sys_prompt = (
        "Ты старший методолог и аналитик операционных процессов банка. "
        "По тексту памятки по работе в АС составь FAQ для сотрудников.\n"
        "Критично:\n"
        "- НЕЛЬЗЯ придумывать функционал, кнопки, экраны или шаги, которых нет в тексте.\n"
        "- Вопросы и ответы должны быть строго отвечаемыми по предоставленному тексту.\n"
        "- Используй профессиональный сленг (АС, карточка, форма, поле, статус, маршрут, сверка, валидация и т.п.), "
        "но не добавляй сущности, которых нет.\n"
        "- В ответах будь подробным, но без домыслов: только то, что следует из текста.\n"
        "- Формат ответа СТРОГО задан ниже, без лишних комментариев.\n"
    )

    out_chunks: List[str] = []

    for page_num, page_text in pages:
        if not page_text.strip():
            continue

        question = (
            "Ниже приведён общий контекст документа (может быть обрезан):\n"
            "----------------------------------------\n"
            f"{full_doc_context}\n"
            "----------------------------------------\n\n"
            f"Ниже приведён текст страницы №{page_num:03d}:\n"
            "----------------------------------------\n"
            f"{page_text}\n"
            "----------------------------------------\n\n"
            "Сгенерируй 3–5 максимально продуманных элементов FAQ по этой странице.\n"
            "Требования:\n"
            "- вопросы должны быть практическими (что делать/как проверить/какие условия/какие статусы/что означает и т.п.);\n"
            "- вопросы не должны повторяться по смыслу;\n"
            "- вопросы должны учитывать контекст документа, но опираться на факты из текста страницы;\n"
            "- используй профессиональный сленг, соответствующий банковским АС;\n\n"
            "Формат ВЫВОДА (строго, повторить блок 3–5 раз):\n\n"
            "ВОПРОС: <текст вопроса>\n\n"
            "ОТВЕТ: <подробный ответ>\n\n"
            f'[SOURCE - "{pamphlet_name} - {page_num:03d}"]\n\n'
            "Правила формата:\n"
            "- после строки [SOURCE - \"...\"] сразу начинается следующий блок или конец ответа;\n"
            "- никаких списков маркерами, никаких заголовков, никаких лишних строк до/после;\n"
            "- в источнике всегда используй ровно этот шаблон и текущий номер страницы.\n"
        )

        faq = giga_free_answer(
            question=question,
            access_token=access_token,
            sys_prompt=sys_prompt,
            max_tokens=output_tokens,
        ).strip()

        out_chunks.append(f"## FAQ — Страница {page_num:03d}\n\n{faq}\n")

    return "\n\n".join(out_chunks).strip() + "\n"


def generate_faq_rows_for_pages(
    pages: List[Tuple[int, str]],
    full_doc_context: str,
    access_token: str,
    pamphlet_name: str,
    output_tokens: int = 10000,
) -> List[Dict[str, str]]:
    """
    Генерируем FAQ и возвращаем список строк для Excel:
      {"question": "...", "answer": "...", "source": "..."}
    """
    md = generate_faq_for_pages(
        pages=pages,
        full_doc_context=full_doc_context,
        access_token=access_token,
        pamphlet_name=pamphlet_name,
        output_tokens=output_tokens,
    )
    rows = _parse_faq_blocks(md)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Генератор FAQ-вопросов по итоговой инструкции (.md).\n"
            "На каждую страницу: 3–5 вопросов. Учёт контекста документа.\n"
            "Требуется доступ к GigaChat (переменные в .env)."
        )
    )
    parser.add_argument(
        "--md",
        type=str,
        required=True,
        help="Путь к markdown-файлу (например out/<pdf>/instructions_merged.md или instructions_incremental.md).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Путь к выходному файлу. По умолчанию создаётся рядом: <input>_faq.xlsx",
    )
    parser.add_argument(
        "--pamphlet-name",
        type=str,
        default="",
        help='Название памятки для строки источника. По умолчанию берётся из имени каталога (например out/<pdf>/...).',
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=10000,
        help="Лимит output tokens (max_tokens) для одного ответа модели. По умолчанию 10000.",
    )

    args = parser.parse_args()
    in_path = Path(args.md)
    if not in_path.exists():
        raise FileNotFoundError(f"Файл не найден: {in_path}")

    md_text = in_path.read_text(encoding="utf-8")

    # Авторизация
    creds = get_creds()
    access_token = creds.get("access_token")
    # cert-mode: токена может не быть
    if not access_token and creds.get("auth_mode") != "cert":
        raise RuntimeError(f"Токен не получен от NGW. Ответ: {creds}")

    # Парсинг страниц: сначала пробуем формат с SOURCE-тегами, иначе — по заголовкам
    by_source = _group_lines_by_source_tags(md_text)
    pages: List[Tuple[int, str]] = []
    if by_source:
        for page_num in sorted(by_source.keys()):
            pages.append((page_num, "\n".join(by_source[page_num])))
        doc_context = _build_doc_context(md_text, max_chars=12000)
    else:
        pages = _split_by_page_headers(md_text)
        doc_context = _build_doc_context(md_text, max_chars=12000)

    if not pages:
        raise ValueError(
            "Не удалось выделить страницы из markdown. "
            "Ожидаю либо заголовки '## Страница NNN', либо строки с [SOURCE: page XXX]."
        )

    # Название памятки для SOURCE
    pamphlet_name = args.pamphlet_name.strip()
    if not pamphlet_name:
        # Типовой путь: out/<pamphlet>/instructions_*.md
        # Если структура другая — fallback на имя файла без расширения
        parent_name = in_path.parent.name
        pamphlet_name = parent_name if parent_name else in_path.stem

    rows = generate_faq_rows_for_pages(
        pages=pages,
        full_doc_context=doc_context,
        access_token=access_token,
        pamphlet_name=pamphlet_name,
        output_tokens=args.output_tokens,
    )

    out_path = Path(args.out) if args.out else in_path.with_name(f"{in_path.stem}_faq.xlsx")
    _rows_to_xlsx(rows, out_path)
    print(f"FAQ (Excel) сохранён: {out_path}")

    stats = get_token_stats()
    print(
        "\nИТОГО по токенам в этом запуске (FAQ):\n"
        f"- prompt_tokens     = {stats.get('prompt_tokens', 0)}\n"
        f"- completion_tokens = {stats.get('completion_tokens', 0)}\n"
        f"- total_tokens      = {stats.get('total_tokens', 0)}"
    )


if __name__ == "__main__":
    main()


