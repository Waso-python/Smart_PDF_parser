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
from generate_faq import generate_faq_rows_for_pages, _build_doc_context
from openpyxl import Workbook


def _source_line(pamphlet_name: str, page_num: int) -> str:
    safe_name = (pamphlet_name or "document").replace('"', "'").strip()
    return f'[SOURCE - "{safe_name} - {page_num:03d}"]'


def stage2_build_instruction_for_page_ocr_only(
    image_path: Path,
    access_token: str | None,
    pamphlet_name: str,
    page_num: int,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """
    Режим OCR-only:
      - один мультимодальный вызов (ocr_instruction_via_rest)
      - instruction.txt = OCR-текст + строка источника
    Без merge.
    """
    ocr_text = ocr_instruction_via_rest(
        str(image_path),
        access_token,
        model=model,
        temperature=temperature,
    ).strip()
    src = _source_line(pamphlet_name, page_num)
    if ocr_text:
        return f"{ocr_text}\n\n{src}\n"
    return f"{src}\n"

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
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """
    Этап 2.
    1) Распознаём скриншот страницы через GigaChat (ocr_instruction_via_rest).
    2) Объединяем текстовый слой и распознанный текст в единую инструкцию
       вторым запросом к GigaChat (giga_free_answer).
    Возвращаем итоговую инструкцию как строку.
    """
    # 2.1. Получаем описание по скриншоту (мультимодальный вызов)
    ocr_description = ocr_instruction_via_rest(
        str(image_path),
        access_token,
        model=model,
        temperature=temperature,
    )

    # 2.2. Читаем текстовый слой страницы
    text_layer = text_path.read_text(encoding="utf-8")

    # 2.3. Формируем запрос на объединение (приоритет: текстовый слой)
    merge_question = (
        "У тебя есть две версии ОДНОЙ И ТОЙ ЖЕ страницы инструкции по работе в АС.\n\n"
        "ВЕРСИЯ A (АВТОРИТЕТНАЯ): текстовый слой страницы (из PDF).\n"
        "Это основной источник истины: он должен определять формулировки, порядок и содержание.\n"
        "----------------------------------------\n"
        f"{text_layer}\n"
        "----------------------------------------\n\n"
        "ВЕРСИЯ B (ВСПОМОГАТЕЛЬНАЯ): текст, полученный OCR по скриншоту той же страницы.\n"
        "OCR может содержать мусор (артефакты, обрывки UI-обвязки, водяные знаки, ошибки распознавания).\n"
        "Используй OCR ТОЛЬКО как подсказку для восстановления явно ПРОПУЩЕННЫХ фрагментов, "
        "если они отсутствуют в версии A, но хорошо читаются в версии B.\n"
        "----------------------------------------\n"
        f"{ocr_description}\n"
        "----------------------------------------\n\n"
        "Твоя задача — сделать один аккуратный итоговый текст этой страницы.\n\n"
        "Строгие правила (критично):\n"
        "1) ПРИОРИТЕТ: если версия A содержит фразу/пункт, а версия B отличается или противоречит — "
        "ВСЕГДА выбирай версию A. Не «улучшай» и не переписывай по OCR.\n"
        "2) OCR-добавления разрешены ТОЛЬКО если:\n"
        "   - в версии A есть очевидный пропуск/пустое место/обрыв строки, и\n"
        "   - в версии B этот же фрагмент читается ясно, и\n"
        "   - добавление не похоже на UI-обвязку/шум/водяной знак/служебный текст.\n"
        "   Если есть сомнение — НЕ добавляй.\n"
        "3) АНТИ-МУСОР: не включай в итоговый текст элементы интерфейса/просмотрщика/браузера "
        "(меню, табы, кнопки «ОК/Отмена/Назад/Далее/Сохранить/Закрыть», навигацию, пагинацию, системные статусы), "
        "водяные знаки (demo/sample/test) и прочие посторонние надписи, даже если OCR их распознал.\n"
        "4) НЕЛЬЗЯ придумывать ни одного нового шага, кнопки, поля, предупреждения или общего совета, "
        "если он явно не присутствует хотя бы в одной из двух версий.\n"
        "5) Можно:\n"
        "   - убирать повторы;\n"
        "   - исправлять переносы строк/дефисы и типографику;\n"
        "   - исправлять явные артефакты OCR (но НЕ переносить OCR-ошибки в итог).\n"
        "6) Итог должен быть максимально близок к версии A по содержанию. "
        "Верни ТОЛЬКО итоговый текст страницы, без комментариев и без разделов «A/B».\n"
    )

    sys_prompt_merge = (
        "Ты опытный методолог и сотрудник кредитного отдела банка. "
        "Твоя задача — строго и аккуратно объединять несколько версий одной и той же страницы "
        "в единый текст БЕЗ добавления новых смыслов.\n"
        "Критично: версия A (текстовый слой PDF) — авторитетная. "
        "Версия B (OCR) — только вспомогательная, её легко загрязняет шум. "
        "Если есть конфликт — выбирай версию A. "
        "OCR используй только для восстановления явных пропусков в версии A.\n"
        "Любая фраза, которой нет в исходных текстах, считается ошибкой. "
        "Не придумывай примеры, рекомендации, служебные фразы и дополнительный функционал."
    )

    merged_instruction = giga_free_answer(
        question=merge_question,
        access_token=access_token,
        sys_prompt=sys_prompt_merge,
        model=model,
        # Для merge держим температуру максимально низкой: меньше «креативности» и меньше заноса OCR-мусора.
        temperature=0.0 if temperature is None else temperature,
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


def run_pipeline(pdf_dir: Path, out_root: Path, mode: str = "full") -> None:
    """
    Запускает все три этапа пайплайна для всех PDF в указанном каталоге.
    """
    creds = get_creds()
    access_token = creds.get("access_token")
    # Пайплайн всегда работает с изображениями (OCR/attachments), поэтому тут нужен токен.
    # mTLS в проекте используется только для текстовых запросов.
    if not access_token:
        raise RuntimeError(
            f"Токен не получен от NGW. Ответ: {creds}. "
            "Для обработки изображений требуется Bearer access_token (OAuth): "
            "проверьте GIGA_ACCESS_KEY и доступ к NGW."
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
                if mode == "ocr_only":
                    instruction = stage2_build_instruction_for_page_ocr_only(
                        image_path=image_path,
                        access_token=access_token,
                        pamphlet_name=pdf_path.stem,
                        page_num=page_num,
                    )
                else:
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

        # Этап 4 (опционально): FAQ в Excel по всем страницам
        if os.getenv("GENERATE_FAQ_XLSX", "0") == "1":
            print("Этап 5: генерация FAQ (Excel) по всем страницам...")
            merged_text = merged_path.read_text(encoding="utf-8") if merged_path.exists() else ""
            doc_ctx = _build_doc_context(merged_text, max_chars=12000)

            pages_for_faq = []
            for info in page_infos:
                page_num = info["page_num"]
                instr_path = info["dir"] / "instruction.txt"
                if instr_path.exists():
                    pages_for_faq.append((page_num, instr_path.read_text(encoding="utf-8")))

            rows = generate_faq_rows_for_pages(
                pages=pages_for_faq,
                full_doc_context=doc_ctx,
                access_token=access_token,
                pamphlet_name=pdf_path.stem,
                output_tokens=int(os.getenv("FAQ_OUTPUT_TOKENS", "10000")),
            )

            # XLSX
            wb = Workbook()
            ws = wb.active
            ws.title = "FAQ"
            ws.append(["Вопрос", "Ответ", "Источник"])
            for r in rows:
                ws.append([r.get("question", ""), r.get("answer", ""), r.get("source", "")])
            ws.column_dimensions["A"].width = 60
            ws.column_dimensions["B"].width = 90
            ws.column_dimensions["C"].width = 40

            faq_xlsx_path = pdf_out_dir / f"{pdf_path.stem}_faq.xlsx"
            wb.save(faq_xlsx_path)
            print(f"Этап 5: FAQ сохранён: {faq_xlsx_path}")

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
            "2) Обработка страниц через GigaChat (full: OCR+merge / ocr_only: только OCR); "
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
    parser.add_argument(
        "--mode",
        type=str,
        default="full",
        choices=["full", "ocr_only"],
        help="Режим обработки: full = OCR+merge; ocr_only = только OCR (instruction.txt из OCR + SOURCE).",
    )

    args = parser.parse_args()
    run_pipeline(pdf_dir=Path(args.pdf_dir), out_root=Path(args.out_dir), mode=args.mode)


if __name__ == "__main__":
    main()


