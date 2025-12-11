import os
import uuid
import json
import datetime
import base64
import io
import requests

from dotenv import load_dotenv

load_dotenv()

# ---------- Настройки ----------

# Основные параметры берём из переменных окружения (.env), с дефолтами под промышленный контур
GIGA_CHAT_AUTH_DATA = os.getenv("GIGA_ACCESS_KEY")
GIGA_CHAT_SCOPE = os.getenv("GIGA_CHAT_SCOPE", "GIGACHAT_API_CORP")

# эндпоинт NGW для получения токена
NGW_URL = os.getenv(
    "GIGA_NGW_URL",
    "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
)

# эндпоинт GigaChat API для чата (/chat/completions)
GIGA_API_URL = os.getenv(
    "GIGA_CHAT_COMPLETIONS_URL",
    "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
)

# эндпоинт GigaChat API для загрузки файлов (изображений)
GIGA_FILES_URL = os.getenv(
    "GIGA_CHAT_FILES_URL",
    "https://gigachat.devices.sberbank.ru/api/v1/files",
)

# Модели для текста и мультимодальных запросов
TEXT_MODEL = os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")
VISION_MODEL = os.getenv("GIGA_VISION_MODEL", "GigaChat-2-Pro")

SYS_PROMPT = (
    "Ты опытный сотрудник кредитного отдела банка. "
    "По изображению с инструкцией по работе в АС:\n"
    "- аккуратно выпиши текст инструкции, который виден на изображении (включая заголовки, подписи и мелкий текст);\n"
    "- можешь слегка структурировать оформление (заголовки, списки), но НЕ добавляй новых шагов, пояснений или примеров;\n"
    "- каждый факт, шаг или формулировка в ответе должен явно присутствовать на изображении;\n"
    "- строго запрещено придумывать несуществующие элементы интерфейса, действия, кнопки или рекомендации;\n"
    "- если какого‑то фрагмента текста на изображении не видно или он обрезан, не восстанавливай его по смыслу и не додумывай."
)

# Глобальная статистика по токенам за время работы процесса
TOKEN_STATS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}


def _update_token_stats(data: dict) -> None:
    """Обновляем глобальную статистику токенов по объекту usage из ответа GigaChat."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            TOKEN_STATS[key] += value


def get_token_stats() -> dict:
    """Вернуть копию статистики токенов."""
    return dict(TOKEN_STATS)


# ---------- Вспомогательные функции ----------

def generate_id() -> str:
    return str(uuid.uuid4())


def get_creds() -> dict:
    """Получаем access_token через NGW (как в твоём коде)."""
    headers = {
        "Authorization": f"Bearer {GIGA_CHAT_AUTH_DATA}",
        "RqUID": generate_id(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"scope": GIGA_CHAT_SCOPE}

    r = requests.post(NGW_URL, headers=headers, data=data, verify=False)
    json_response = json.loads(r.text)
    return json_response


def upload_image_to_files(path: str, access_token: str) -> str:
    """
    Загружаем изображение в хранилище GigaChat и получаем идентификатор файла,
    который потом передаётся в messages[*].attachments, как описано в доке.
    """
    filename = os.path.basename(path)
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".jpg", ".jpeg"):
        mime_type = "image/jpeg"
    elif ext == ".png":
        mime_type = "image/png"
    else:
        # Библиотека и API в любом случае поймут JPEG/PNG, другие форматы лучше не использовать
        raise ValueError("Поддерживаются только изображения JPG/JPEG или PNG.")

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    with open(path, "rb") as f:
        files = {
            "file": (filename, f, mime_type),
        }
        # Согласно спецификации FileUpload, дополнительно можно указать purpose=general
        data = {
            "purpose": "general",
        }
        resp = requests.post(
            GIGA_FILES_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=120,
            verify=False,
        )

    # Отдельно обрабатываем 400, чтобы увидеть текст ошибки от GigaChat и не падать трассировкой
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 400:
            try:
                err_payload = resp.json()
            except ValueError:
                err_text = resp.text
            else:
                err_text = json.dumps(err_payload, ensure_ascii=False, indent=2)
            raise ValueError(
                "Ошибка загрузки файла в GigaChat (HTTP 400 Bad Request).\n"
                "Проверьте формат запроса к /api/v1/files.\n"
                f"Ответ сервера:\n{err_text}"
            ) from e
        raise
    data = resp.json()
    # загрузка файла токены не тарифицирует по chat/completions, usage здесь нет

    # Пытаемся аккуратно вытащить идентификатор файла из разных возможных полей
    file_id = data.get("id") or data.get("file_id") or data.get("fileId")
    if not file_id:
        raise RuntimeError(f"Не удалось получить идентификатор файла из ответа GigaChat: {data}")

    return file_id


# ---------- Текстовый диалог через REST ----------

def giga_free_answer(
    question: str,
    access_token: str,
    sys_prompt: str = "Ты банковский работник, ответь на заданный вопрос максимально лаконично",
    history=None,
) -> str:
    """
    Обычный текстовый запрос к GigaChat через REST (без картинок).
    Заодно учитываем usage из ответа для подсчёта токенов.
    """
    if history is None:
        history = []

    messages = []
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})

    # История (если когда‑нибудь понадобится)
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("system", "user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": question})

    payload = {
        "model": TEXT_MODEL,
        "temperature": 0.01,
        "messages": messages,
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        GIGA_API_URL,
        headers=headers,
        json=payload,
        timeout=120,
        verify=False,
    )

    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        # Для текстовых запросов достаточно пробросить ошибку наверх
        raise e

    data = resp.json()
    _update_token_stats(data)

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, str):
        return content
    return str(content)


# ---------- Распознавание инструкции с изображения через REST ----------

def ocr_instruction_via_rest(image_path: str, access_token: str) -> str:
    """
    Отправляем в GigaChat-Pro изображение + промпт
    и получаем подробное текстовое описание инструкции.[web:67][web:69]
    """
    # 1. Загружаем изображение в файловое хранилище GigaChat и получаем file_id
    file_id = upload_image_to_files(image_path, access_token)

    # 2. Строим payload строго по схеме из readme_gigachat_api.md:
    #    model + messages[ {role, content, attachments: [file_id]} ]
    payload = {
        "model": VISION_MODEL,
        "temperature": 0.01,
        "messages": [
            {
                "role": "system",
                "content": SYS_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "На изображении показана инструкция по работе в АС "
                    "в кредитном отделе банка. "
                    "Твоя задача — переписать текст этой инструкции практически дословно, "
                    "можно только чуть структурировать оформление (заголовки, списки).\n\n"
                    "Не добавляй никаких новых шагов, рекомендаций или обобщающих фраз, "
                    "которых нет на изображении. Если чего‑то нет на картинке, не придумывай это."
                ),
                "attachments": [file_id],
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        GIGA_API_URL,
        headers=headers,
        json=payload,
        timeout=120,
        verify=False,
    )

    # Обработка ошибок HTTP (в т.ч. 413 и 400)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 413:
            # Возвращаем человеко-читаемое сообщение вместо падения скрипта
            return (
                "Ошибка GigaChat: HTTP 413 Request Entity Too Large. "
                "Изображение или запрос слишком большой. "
                "Попробуйте уменьшить разрешение/размер файла и повторить попытку."
            )
        if resp.status_code == 400:
            # Показываем, что именно не понравилось API, вместо необработанного исключения
            try:
                err_payload = resp.json()
            except ValueError:
                err_text = resp.text
            else:
                err_text = json.dumps(err_payload, ensure_ascii=False, indent=2)
            return (
                "Ошибка GigaChat: HTTP 400 Bad Request.\n"
                "Проверьте корректность модели и формата запроса.\n"
                f"Ответ сервера:\n{err_text}"
            )
        # Для остальных ошибок — пробрасываем исключение дальше
        raise e

    data = resp.json()
    _update_token_stats(data)

    # мультимодальные ответы GigaChat обычно возвращают content как массив блоков[web:62][web:67]
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                texts.append(block.get("text", ""))
        if texts:
            return "\n".join(texts)
    # fallback: если вдруг контент строкой
    if isinstance(content, str):
        return content
    return str(content)


# ---------- main ----------

def main():
    creds = get_creds()
    print("Ответ от NGW:", creds)

    GIGA_ACCESS_TOKEN = creds.get("access_token")
    GIGA_EXPIRES_AT = creds.get("expires_at")

    if not GIGA_ACCESS_TOKEN:
        print("Токен не получен! Код/сообщение от NGW:", creds.get("code"), creds.get("message"))
        return

    if GIGA_EXPIRES_AT:
        date = datetime.datetime.fromtimestamp(int(GIGA_EXPIRES_AT) // 1000)
        print("Access Token истекает - ", date)
    else:
        print("Поле 'expires_at' отсутствует или пустое, пропускаю расчёт даты")

    # 1. Пример обычного текстового запроса (как раньше)
    question = "не могу рассчитать риск сегмент"
    answer = giga_free_answer(question, GIGA_ACCESS_TOKEN)
    print(f"answer (text) - {answer}")

    # 2. Пример распознавания инструкции по скрину
    image_path = "123.jpg"  # сюда положи скрин/фото инструкции
    try:
        description = ocr_instruction_via_rest(image_path, GIGA_ACCESS_TOKEN)
    except ValueError as e:
        # Ловим ошибки сжатия/размера изображения и выводим аккуратное сообщение без трассировки
        print(str(e))
        return

    print("\nПодробное описание инструкции по изображению:\n")
    print(description)


if __name__ == "__main__":
    main()
