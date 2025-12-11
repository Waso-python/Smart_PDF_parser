import argparse
import os
from pathlib import Path
from typing import List, Dict

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise ImportError(
        "Для работы пайплайна требуется библиотека PyMuPDF (пакет 'pymupdf'). "
        "Установите её командой: pip install pymupdf"
    ) from e

from img_parse import get_creds, giga_free_answer, ocr_instruction_via_rest, get_token_stats


def stage1_extract_pages(pdf_path: Path, out_root: Path) -> List[Dict]:
    """
    Этап 1.
    Для каждого PDF:
      - создаём каталог <out_root>/<pdf_name_without_ext>/
      - для каждой страницы создаём подкаталог page_XXX/
      - сохраняем текстовый слой страницы в page_XXX/page.txt
      - сохраняем скриншот страницы в page_XXX/page.jpg
    Возвращаем список словарей с путями для дальнейших этапов.
    """
    doc = fitz.open(pdf_path)
    pdf_dir = out_root / pdf_path.stem
    pdf_dir.mkdir(parents=True, exist_ok=True)

    page_infos: List[Dict] = []

    for page_index, page in enumerate(doc, start=1):
        page_dir = pdf_dir / f"page_{page_index:03d}"
        page_dir.mkdir(exist_ok=True)

        # Текстовый слой
        text = page.get_text("text")
        text_path = page_dir / "page.txt"
        text_path.write_text(text, encoding="utf-8")

        # Скриншот страницы
        pix = page.get_pixmap(dpi=150)
        image_path = page_dir / "page.jpg"
        pix.save(str(image_path))

        page_infos.append(
            {
                "page_num": page_index,
                "dir": page_dir,
                "text_path": text_path,
                "image_path": image_path,
            }
        )

    return page_infos


def stage2_build_instruction_for_page(
    text_path: Path,
    image_path: Path,
    access_token: str,
) -> str:
    """
    Этап 2.
    1) Распознаём скриншот страницы через GigaChat (ocr_instruction_via_rest).
    2) Объединяем текстовый слой и распознанный текст в единую инструкцию
       вторым запросом к GigaChat (giga_free_answer).
    Возвращаем итоговую инструкцию как строку.
    """
    # 2.1. Получаем описание по скриншоту (мультимодальный вызов)
    ocr_description = ocr_instruction_via_rest(str(image_path), access_token)

    # 2.2. Читаем текстовый слой страницы
    text_layer = text_path.read_text(encoding="utf-8")

    # 2.3. Формируем запрос на объединение
    merge_question = (
        "У тебя есть две версии ОДНОЙ И ТОЙ ЖЕ страницы инструкции по работе в АС.\n\n"
        "Первая версия – текстовый слой страницы (из PDF):\n"
        "----------------------------------------\n"
        f"{text_layer}\n"
        "----------------------------------------\n\n"
        "Вторая версия – текст, полученный по скриншоту той же страницы:\n"
        "----------------------------------------\n"
        f"{ocr_description}\n"
        "----------------------------------------\n\n"
        "Твоя задача — сделать один аккуратный, объединённый текст этой САМОЙ страницы.\n\n"
        "Строгие правила:\n"
        "1) НЕЛЬЗЯ придумывать ни одного нового шага, пункта, кнопки, предупреждения или общего совета, "
        "если он явно не присутствует хотя бы в одной из двух версий.\n"
        "2) НЕЛЬЗЯ добавлять общие фразы вроде «обратитесь в справку/техподдержку», "
        "если они прямо не написаны в исходных текстах.\n"
        "3) Можно:\n"
        "   - убирать повторы;\n"
        "   - исправлять явные артефакты OCR;\n"
        "   - немного переформулировать фразы, НЕ меняя смысл и не расширяя его.\n"
        "4) Каждый факт и каждое действие в итоговом тексте должно быть дословно или почти дословно "
        "обосновано хотя бы одной из двух версий сверху.\n"
        "5) Если информации мало, просто перепиши её аккуратно и ничего не добавляй.\n"
    )

    sys_prompt_merge = (
        "Ты опытный методолог и сотрудник кредитного отдела банка. "
        "Твоя задача — строго и аккуратно объединять несколько версий одной и той же инструкции "
        "в единый текст БЕЗ добавления новых смыслов. "
        "Любая фраза, которой нет в исходных текстах, считается ошибкой. "
        "Не придумывай примеры, рекомендации, служебные фразы и дополнительный функционал."
    )

    merged_instruction = giga_free_answer(
        question=merge_question,
        access_token=access_token,
        sys_prompt=sys_prompt_merge,
    )

    return merged_instruction


def stage3_merge_pdf_instructions(pdf_dir: Path) -> Path:
    """
    Этап 3.
    Склеиваем все итоговые инструкции по страницам в один документ.
    Ожидаем, что в каждом каталоге page_XXX лежит файл instruction.txt.
    Возвращаем путь к итоговому .md файлу.
    """
    page_dirs = sorted(
        [p for p in pdf_dir.iterdir() if p.is_dir() and p.name.startswith("page_")]
    )

    chunks = []
    for page_dir in page_dirs:
        page_num_str = page_dir.name.split("_", 1)[-1]
        instr_path = page_dir / "instruction.txt"
        if not instr_path.exists():
            continue
        text = instr_path.read_text(encoding="utf-8").strip()
        if not text:
            continue

        chunks.append(f"## Страница {page_num_str}\n\n{text}\n")

    merged_path = pdf_dir / "instructions_merged.md"
    merged_path.write_text("\n\n".join(chunks), encoding="utf-8")
    return merged_path


def stage4_build_incremental_context(pdf_dir: Path, access_token: str) -> Path:
    """
    Этап 4.
    Инкрементально наращиваем «смысл» инструкции по мере чтения страниц:
      - для страницы 1 контекст = её инструкция;
      - для каждой следующей страницы учитываем уже собранный контекст + текущую инструкцию;
      - работаем ТОЛЬКО с текстом (instruction.txt), без картинок.

    На выходе:
      - по каждой странице: instruction_with_context.txt (контекст до этой страницы включительно);
      - общий файл: instructions_incremental.md с полной инструкцией по документу.
    """
    page_dirs = sorted(
        [p for p in pdf_dir.iterdir() if p.is_dir() and p.name.startswith("page_")]
    )

    if not page_dirs:
        return pdf_dir / "instructions_incremental.md"

    sys_prompt_incremental = (
        "Ты опытный методолог и сотрудник кредитного отдела банка. "
        "Ты собираешь единую подробную инструкцию по работе в АС из нескольких страниц.\n"
        "- Ты НИКОГДА не придумываешь новых шагов, сценариев, кнопок или рекомендаций,\n"
        "  которых нет в текстах страниц.\n"
        "- Твоя особенность — ты всегда помечаешь каждый смысловой элемент тегом источника "
        "вида [SOURCE: page XXX], где XXX — номер страницы, на которой этот элемент появился.\n"
        "- Ты можешь только:\n"
        "  * объединять и упорядочивать уже имеющиеся шаги;\n"
        "  * убирать повторы;\n"
        "  * НЕ менять смысл уже существующих элементов.\n"
        "- Любая новая идея, не подтверждённая текстом страниц, считается ошибкой."
    )

    combined_text: str | None = None

    for idx, page_dir in enumerate(page_dirs, start=1):
        instr_path = page_dir / "instruction.txt"
        if not instr_path.exists():
            continue
        page_text = instr_path.read_text(encoding="utf-8").strip()
        if not page_text:
            continue

        if combined_text is None:
            # Первая страница — формируем элементы сразу с тегами источника
            question = (
                f"Перед тобой текст страницы №{idx} инструкции по работе в АС:\n"
                "----------------------------------------\n"
                f"{page_text}\n"
                "----------------------------------------\n\n"
                "Сформируй список смысловых элементов (шаги, правила, предупреждения, заголовки разделов) "
                "только по этому тексту.\n\n"
                "Требования к формату:\n"
                f"- каждый элемент пиши с новой строки;\n"
                f"- в КОНЦЕ каждой строки добавь тег вида [SOURCE: page {idx:03d}];\n"
                "- не добавляй информацию, которой нет в тексте страницы.\n"
                "- не добавляй никакие пояснения, комментарии или примеры от себя."
            )

            combined_text = giga_free_answer(
                question=question,
                access_token=access_token,
                sys_prompt=sys_prompt_incremental,
            )
        else:
            # Инкрементальное уточнение/расширение с учётом новой страницы
            question = (
                f"У тебя уже есть собранная инструкция по страницам 1–{idx-1} "
                "с тегами источников [SOURCE: page XXX]:\n"
                "----------------------------------------\n"
                f"{combined_text}\n"
                "----------------------------------------\n\n"
                f"И есть текст новой страницы №{idx}:\n"
                "----------------------------------------\n"
                f"{page_text}\n"
                "----------------------------------------\n\n"
                "Обнови общую инструкцию так, чтобы она отражала страницы 1–"
                f"{idx} включительно.\n\n"
                "Строгие правила:\n"
                "1) НЕ удаляй и НЕ изменяй существующие строки и их теги [SOURCE: page ...], "
                "можно только добавлять новые строки.\n"
                "2) Для новых смысловых элементов, которые появляются только на странице "
                f"№{idx}, добавляй строки с тегом [SOURCE: page {idx:03d}].\n"
                "3) НЕЛЬЗЯ придумывать новые функции, кнопки, шаги или рекомендации, "
                "если их нет ни в одной из страниц.\n"
                "4) Если новая страница почти ничего не добавляет, можешь вернуть текст почти "
                "без изменений.\n"
                "5) Верни только итоговый текст инструкции с тегами, без пояснений и комментариев."
            )

            combined_text = giga_free_answer(
                question=question,
                access_token=access_token,
                sys_prompt=sys_prompt_incremental,
            )

        # Сохраняем контекст до текущей страницы включительно
        ctx_path = page_dir / "instruction_with_context.txt"
        ctx_path.write_text(combined_text, encoding="utf-8")

    # Итоговый файл по всему документу
    incremental_path = pdf_dir / "instructions_incremental.md"
    if combined_text is None:
        incremental_path.write_text("", encoding="utf-8")
    else:
        incremental_path.write_text(combined_text, encoding="utf-8")

    return incremental_path


def run_pipeline(pdf_dir: Path, out_root: Path) -> None:
    """
    Запускает все три этапа пайплайна для всех PDF в указанном каталоге.
    """
    creds = get_creds()
    access_token = creds.get("access_token")
    if not access_token:
        raise RuntimeError(
            f"Токен не получен от NGW. Ответ: {creds}. "
            "Проверьте переменную окружения GIGA_ACCESS_KEY и доступ к NGW."
        )

    pdf_dir = pdf_dir.resolve()
    out_root = out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"В каталоге {pdf_dir} не найдено PDF-файлов.")
        return

    for pdf_path in pdf_files:
        print(f"\n=== Обработка PDF: {pdf_path.name} ===")

        # Этап 1: извлечение страниц
        page_infos = stage1_extract_pages(pdf_path, out_root)
        print(f"Этап 1: извлечено страниц: {len(page_infos)}")

        # Этап 2: GigaChat для каждой страницы
        for info in page_infos:
            page_num = info["page_num"]
            page_dir = info["dir"]
            text_path = info["text_path"]
            image_path = info["image_path"]

            print(f"Этап 2: страница {page_num} ({page_dir})")
            try:
                instruction = stage2_build_instruction_for_page(
                    text_path=text_path,
                    image_path=image_path,
                    access_token=access_token,
                )
            except ValueError as e:
                # Ошибки размера/загрузки/валидации обрабатываем мягко, но логируем
                print(f"  Ошибка при обработке страницы {page_num}: {e}")
                continue

            instr_path = page_dir / "instruction.txt"
            instr_path.write_text(instruction, encoding="utf-8")

        # Этап 3: склейка по PDF (страницы как независимые инструкции)
        pdf_out_dir = out_root / pdf_path.stem
        merged_path = stage3_merge_pdf_instructions(pdf_out_dir)
        print(f"Этап 3: итоговый документ (страницы по отдельности): {merged_path}")

        # Этап 4: инкрементальное накопление смысла по страницам
        incremental_path = stage4_build_incremental_context(pdf_out_dir, access_token)
        print(f"Этап 4: итоговый документ с накопленным контекстом: {incremental_path}")

    # После обработки всех PDF выводим суммарное потребление токенов
    stats = get_token_stats()
    print(
        "\nИТОГО по всем запросам GigaChat в этом запуске скрипта:\n"
        f"- prompt_tokens     = {stats.get('prompt_tokens', 0)}\n"
        f"- completion_tokens = {stats.get('completion_tokens', 0)}\n"
        f"- total_tokens      = {stats.get('total_tokens', 0)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Пайплайн обработки памяток по работе в АС:\n"
            "1) Разбиение PDF на страницы (текст + скриншот); "
            "2) Обработка скриншотов через GigaChat и объединение с текстовым слоем; "
            "3) Склейка итоговых инструкций в один документ."
        )
    )
    parser.add_argument(
        "--pdf-dir",
        type=str,
        required=True,
        help="Каталог с исходными PDF-памятками.",
		default="pdfs",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        required=True,
        help="Каталог, куда складывать результаты пайплайна.",
		default="out",
    )

    args = parser.parse_args()
    run_pipeline(pdf_dir=Path(args.pdf_dir), out_root=Path(args.out_dir))


if __name__ == "__main__":
    main()


