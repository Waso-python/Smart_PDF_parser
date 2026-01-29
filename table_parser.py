"""
Модуль для распознавания таблиц со слайдов/страниц документов.
Таблицы преобразуются в текстовый формат (Markdown) для удобства загрузки в RAG.

Использование:
    python table_parser.py --image path/to/image.jpg --out table.md
    python table_parser.py --image path/to/image.jpg --format markdown
    python table_parser.py --image path/to/image.jpg --format text
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from img_parse import (
    get_creds,
    giga_free_answer,
    ocr_instruction_via_rest,
    upload_image_to_files,
    get_token_stats,
    GIGA_TABLE_TIMEOUT,
)

load_dotenv()

# Модель и температура для распознавания таблиц
TABLE_MODEL = os.getenv("GIGA_TABLE_MODEL", os.getenv("GIGA_VISION_MODEL", "GigaChat-2-Pro"))
TABLE_TEMPERATURE = float(os.getenv("GIGA_TABLE_TEMPERATURE", "0.01"))


SYS_PROMPT_TABLE = """Ты опытный аналитик данных и методолог банка.
Твоя задача — распознать таблицу на изображении и представить её в структурированном текстовом виде.

Строгие правила:
1. Распознай ВСЕ данные таблицы: заголовки столбцов, заголовки строк, все ячейки.
2. Сохрани структуру таблицы: количество столбцов и строк должно соответствовать оригиналу.
3. Если ячейка пустая — оставь её пустой (не придумывай данные).
4. Если текст в ячейке обрезан/не читается — напиши [нечитаемо] или оставь как есть, если частично видно.
5. НЕ добавляй данные, которых нет на изображении.
6. НЕ интерпретируй и не комментируй содержимое — только распознай и структурируй.
7. Если на изображении несколько таблиц — распознай каждую отдельно с заголовком.
8. Моковые/примерные данные (ФИО, номера, суммы) можно оставлять как есть — это справочная информация.
"""


def parse_table_from_image(
    image_path: str,
    access_token: str | None = None,
    output_format: str = "markdown",
    model: str | None = None,
    temperature: float | None = None,
    context: str | None = None,
) -> str:
    """
    Распознаёт таблицу с изображения и возвращает её в текстовом формате.

    Args:
        image_path: Путь к изображению с таблицей
        access_token: Токен доступа GigaChat (если None — получаем автоматически)
        output_format: Формат вывода: "markdown", "text", "csv"
        model: Модель GigaChat (по умолчанию из env)
        temperature: Температура генерации
        context: Дополнительный контекст (название документа, описание таблицы)

    Returns:
        Текстовое представление таблицы
    """
    if access_token is None:
        creds = get_creds()
        access_token = creds.get("access_token")

    # Обработка изображений в проекте остаётся токенной: для attachments/upload нужен Bearer-токен.
    if not access_token:
        raise RuntimeError(
            "Для распознавания таблиц по изображению требуется Bearer access_token (OAuth). "
            "Настройте GIGA_ACCESS_KEY/NGW, либо передайте access_token явно."
        )

    model = model or TABLE_MODEL
    temperature = temperature if temperature is not None else TABLE_TEMPERATURE

    # Загружаем изображение в GigaChat
    file_id = upload_image_to_files(image_path, access_token)

    # Формируем промпт в зависимости от формата
    format_instructions = _get_format_instructions(output_format)

    context_block = ""
    if context:
        context_block = f"\nКОНТЕКСТ: {context}\n"

    user_prompt = f"""На изображении показана таблица (или несколько таблиц) из документа/слайда.
{context_block}
Твоя задача:
1. Внимательно распознай все данные таблицы.
2. Представь таблицу в следующем формате:

{format_instructions}

Важно:
- Распознай ВСЕ столбцы и строки таблицы.
- Сохрани порядок столбцов и строк как на изображении.
- Если есть заголовок таблицы над ней — включи его.
- Если таблица большая — распознай полностью, не сокращай.
- Если на изображении несколько таблиц — распознай каждую отдельно.

Верни ТОЛЬКО таблицу в указанном формате, без пояснений до и после.
"""

    # Вызываем GigaChat с изображением
    from img_parse import GIGA_API_URL, _post_with_optional_token, _update_token_stats

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": SYS_PROMPT_TABLE},
            {"role": "user", "content": user_prompt, "attachments": [file_id]},
        ],
    }

    headers = {"Content-Type": "application/json"}

    resp = _post_with_optional_token(
        GIGA_API_URL,
        headers_base=headers,
        access_token=access_token,
        json_payload=payload,
        timeout=GIGA_TABLE_TIMEOUT,  # таблицы могут быть большими
        force_token_auth=True,
    )

    resp.raise_for_status()
    data = resp.json()
    _update_token_stats(data)

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                texts.append(block.get("text", ""))
        if texts:
            return "\n".join(texts).strip()
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _get_format_instructions(output_format: str) -> str:
    """Возвращает инструкции по формату вывода."""
    if output_format == "markdown":
        return """ФОРМАТ: Markdown-таблица

Пример:
| Столбец 1 | Столбец 2 | Столбец 3 |
|-----------|-----------|-----------|
| Значение  | Значение  | Значение  |
| Значение  | Значение  | Значение  |

Правила:
- Первая строка — заголовки столбцов
- Вторая строка — разделитель (|---|---|)
- Далее — строки данных
- Каждая ячейка отделена символом |
"""
    elif output_format == "csv":
        return """ФОРМАТ: CSV (значения через точку с запятой)

Пример:
Столбец 1;Столбец 2;Столбец 3
Значение;Значение;Значение
Значение;Значение;Значение

Правила:
- Первая строка — заголовки столбцов
- Разделитель — точка с запятой (;)
- Каждая строка таблицы — новая строка в выводе
- Если в ячейке есть точка с запятой — заключи значение в кавычки
"""
    else:  # text
        return """ФОРМАТ: Текстовая таблица с выравниванием

Пример:
┌───────────┬───────────┬───────────┐
│ Столбец 1 │ Столбец 2 │ Столбец 3 │
├───────────┼───────────┼───────────┤
│ Значение  │ Значение  │ Значение  │
│ Значение  │ Значение  │ Значение  │
└───────────┴───────────┴───────────┘

Или простой вариант:
Столбец 1     | Столбец 2     | Столбец 3
--------------+---------------+--------------
Значение      | Значение      | Значение

Правила:
- Выравнивай столбцы для читаемости
- Используй символы для границ таблицы или простые разделители
"""


def parse_table_interactive(
    image_path: str,
    access_token: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """
    Интерактивный режим: сначала показывает что видит на изображении,
    потом уточняет у пользователя детали.
    """
    if access_token is None:
        creds = get_creds()
        access_token = creds.get("access_token")

    if not access_token:
        raise RuntimeError(
            "Интерактивный разбор таблицы требует Bearer access_token (OAuth), "
            "потому что использует загрузку изображения/attachments."
        )

    # Шаг 1: Общее описание
    print("Анализирую изображение...")
    description = ocr_instruction_via_rest(
        image_path,
        access_token,
        model=model or TABLE_MODEL,
        temperature=temperature or TABLE_TEMPERATURE,
    )
    print("\n=== Что вижу на изображении ===")
    print(description)
    print("=" * 40)

    # Шаг 2: Уточнение
    print("\nТеперь распознаю таблицу...")
    table_md = parse_table_from_image(
        image_path,
        access_token=access_token,
        output_format="markdown",
        model=model,
        temperature=temperature,
    )

    return table_md


def refine_table(
    table_text: str,
    instructions: str,
    access_token: str | None = None,
    model: str | None = None,
) -> str:
    """
    Уточняет/исправляет распознанную таблицу по инструкциям пользователя.

    Args:
        table_text: Текущий текст таблицы
        instructions: Инструкции по исправлению (например, "добавь столбец X", "исправь значение в строке 3")
        access_token: Токен GigaChat
        model: Модель

    Returns:
        Исправленная таблица
    """
    if access_token is None:
        creds = get_creds()
        access_token = creds.get("access_token")

    model = model or os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")

    prompt = f"""Ниже приведена распознанная таблица:

{table_text}

Инструкции по исправлению от пользователя:
{instructions}

Выполни указанные исправления и верни исправленную таблицу в том же формате.
Если инструкции непонятны или невыполнимы — верни таблицу без изменений и добавь комментарий в конце.
"""

    result = giga_free_answer(
        question=prompt,
        access_token=access_token,
        sys_prompt="Ты помощник для работы с таблицами. Выполняй инструкции точно, не добавляй лишнего.",
        model=model,
    )

    return result.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Распознавание таблиц со слайдов/изображений для RAG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:

  # Распознать таблицу в Markdown
  python table_parser.py --image slide.jpg

  # Распознать в CSV формате
  python table_parser.py --image slide.jpg --format csv

  # Сохранить в файл
  python table_parser.py --image slide.jpg --out table.md

  # С контекстом документа
  python table_parser.py --image slide.jpg --context "Таблица тарифов из памятки по кредитам"

  # Интерактивный режим (сначала описание, потом таблица)
  python table_parser.py --image slide.jpg --interactive
""",
    )
    parser.add_argument(
        "--image",
        type=str,
        required=True,
        help="Путь к изображению с таблицей (JPG/PNG)",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["markdown", "text", "csv"],
        default="markdown",
        help="Формат вывода таблицы (по умолчанию: markdown)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Путь для сохранения результата (если не указан — вывод в консоль)",
    )
    parser.add_argument(
        "--context",
        type=str,
        default="",
        help="Контекст: название документа или описание таблицы",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Интерактивный режим: сначала описание изображения, потом таблица",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Модель GigaChat (по умолчанию из GIGA_TABLE_MODEL или GIGA_VISION_MODEL)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Температура генерации (по умолчанию 0.01)",
    )

    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Ошибка: файл не найден: {image_path}")
        return 1

    model = args.model.strip() if args.model else None
    temperature = args.temperature

    try:
        if args.interactive:
            result = parse_table_interactive(
                str(image_path),
                model=model,
                temperature=temperature,
            )
        else:
            result = parse_table_from_image(
                str(image_path),
                output_format=args.format,
                model=model,
                temperature=temperature,
                context=args.context.strip() if args.context else None,
            )

        if args.out:
            out_path = Path(args.out)
            out_path.write_text(result, encoding="utf-8")
            print(f"Таблица сохранена: {out_path}")
        else:
            print("\n" + "=" * 50)
            print("РАСПОЗНАННАЯ ТАБЛИЦА:")
            print("=" * 50)
            print(result)
            print("=" * 50)

        # Статистика токенов
        stats = get_token_stats()
        print(
            f"\nТокены: prompt={stats.get('prompt_tokens', 0)}, "
            f"completion={stats.get('completion_tokens', 0)}, "
            f"total={stats.get('total_tokens', 0)}"
        )

    except Exception as e:
        print(f"Ошибка: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
