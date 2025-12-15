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

# ---------- mTLS / сертификаты (опционально) ----------
# Если указаны сертификаты, то запросы выполняются с client-certificate (mTLS).
# При наличии сертификатов мы СНАЧАЛА пробуем выполнить запрос без Bearer-токена,
# и только при 401/403 (и наличии access_token) повторяем запрос с Authorization.
GIGA_CLIENT_CERT = (os.getenv("GIGA_CLIENT_CERT") or "").strip()
GIGA_CLIENT_KEY = (os.getenv("GIGA_CLIENT_KEY") or "").strip()
GIGA_CA_BUNDLE = (os.getenv("GIGA_CA_BUNDLE") or "").strip()
GIGA_TLS_VERIFY = (os.getenv("GIGA_TLS_VERIFY", "0") or "").strip().lower() in ("1", "true", "yes", "on")
GIGA_FORCE_TOKEN_AUTH = (os.getenv("GIGA_FORCE_TOKEN_AUTH", "0") or "").strip().lower() in ("1", "true", "yes", "on")

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
TEXT_TEMPERATURE = float(os.getenv("GIGA_TEXT_TEMPERATURE", "0.01"))
VISION_TEMPERATURE = float(os.getenv("GIGA_VISION_TEMPERATURE", "0.01"))

SYS_PROMPT = (
    "Ты опытный сотрудник кредитного отдела банка. "
    "По изображению с инструкцией по работе в АС:\n"
    "- аккуратно выпиши текст инструкции, который виден на изображении (включая заголовки, подписи и мелкий текст);\n"
    "- можешь слегка структурировать оформление (заголовки, списки), но НЕ добавляй новых шагов, пояснений или примеров;\n"
    "- каждый факт, шаг или формулировка в ответе должен явно присутствовать на изображении;\n"
    "- строго запрещено придумывать несуществующие элементы интерфейса, действия, кнопки или рекомендации;\n"
    "- если какого‑то фрагмента текста на изображении не видно или он обрезан, не восстанавливай его по смыслу и не додумывай."
	"- если на странице приведен скриншот элемента интерфейса АС, не приводи дословное содержание, опиши смысл иллюстрации в рамках текущей инструкции"
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


def _transport_kwargs() -> dict:
    """
    Общие kwargs для requests: verify + cert.
    """
    verify = GIGA_CA_BUNDLE if GIGA_CA_BUNDLE else (True if GIGA_TLS_VERIFY else False)
    kwargs: dict = {"verify": verify}
    if GIGA_CLIENT_CERT:
        if GIGA_CLIENT_KEY:
            kwargs["cert"] = (GIGA_CLIENT_CERT, GIGA_CLIENT_KEY)
        else:
            # поддержка "combined PEM" (cert+key в одном файле)
            kwargs["cert"] = GIGA_CLIENT_CERT
    return kwargs


def _post_with_optional_token(
    url: str,
    headers_base: dict,
    access_token: str | None,
    *,
    json_payload: dict | None = None,
    data_payload: dict | None = None,
    files_payload: dict | None = None,
    timeout: int = 120,
) -> requests.Response:
    """
    Логика приоритета:
    - если указаны client-сертификаты: сначала пробуем запрос БЕЗ Authorization
      (cert-auth), а если получили 401/403 и есть access_token — повторяем с Bearer.
    - если сертификатов нет: работаем только по токену (Authorization обязателен).
    """
    has_cert = bool(GIGA_CLIENT_CERT)
    headers_token = dict(headers_base)
    if access_token:
        headers_token["Authorization"] = f"Bearer {access_token}"

    if not has_cert and not access_token:
        raise RuntimeError(
            "Не задан access_token, а client-сертификаты не настроены. "
            "Либо настройте OAuth (GIGA_ACCESS_KEY), либо mTLS (GIGA_CLIENT_CERT/KEY)."
        )

    kwargs = _transport_kwargs()

    # 1) cert-first
    if has_cert and not GIGA_FORCE_TOKEN_AUTH:
        resp = requests.post(
            url,
            headers=headers_base,
            json=json_payload,
            data=data_payload,
            files=files_payload,
            timeout=timeout,
            **kwargs,
        )
        if resp.status_code in (401, 403) and access_token:
            resp2 = requests.post(
                url,
                headers=headers_token,
                json=json_payload,
                data=data_payload,
                files=files_payload,
                timeout=timeout,
                **kwargs,
            )
            return resp2
        return resp

    # 2) token-only
    return requests.post(
        url,
        headers=headers_token,
        json=json_payload,
        data=data_payload,
        files=files_payload,
        timeout=timeout,
        **kwargs,
    )


def get_creds() -> dict:
    """
    Получаем access_token через NGW (OAuth).
    Если настроены сертификаты и OAuth-ключ не задан, возвращаем режим cert-auth без токена.
    """
    if GIGA_CLIENT_CERT and not GIGA_CHAT_AUTH_DATA:
        return {"access_token": None, "auth_mode": "cert"}

    headers = {
        "RqUID": generate_id(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    # OAuth ключ
    if GIGA_CHAT_AUTH_DATA:
        headers["Authorization"] = f"Bearer {GIGA_CHAT_AUTH_DATA}"
    data = {"scope": GIGA_CHAT_SCOPE}

    r = requests.post(NGW_URL, headers=headers, data=data, timeout=60, **_transport_kwargs())
    json_response = json.loads(r.text)
    json_response.setdefault("auth_mode", "token")
    return json_response


def upload_image_to_files(path: str, access_token: str | None) -> str:
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

    headers_base = {}
    with open(path, "rb") as f:
        content = f.read()
        files = {"file": (filename, content, mime_type)}
        # Согласно спецификации FileUpload, дополнительно можно указать purpose=general
        data = {
            "purpose": "general",
        }
        resp = _post_with_optional_token(
            GIGA_FILES_URL,
            headers_base=headers_base,
            access_token=access_token,
            files_payload=files,
            data_payload=data,
            timeout=120,
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
    access_token: str | None,
    sys_prompt: str = "Ты банковский работник, ответь на заданный вопрос максимально лаконично",
    history=None,
    max_tokens: int | None = None,
    model: str | None = None,
    temperature: float | None = None,
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
        "model": model or TEXT_MODEL,
        "temperature": float(temperature) if temperature is not None else TEXT_TEMPERATURE,
        "messages": messages,
    }
    if isinstance(max_tokens, int):
        payload["max_tokens"] = max_tokens

    headers = {
        "Content-Type": "application/json",
    }

    resp = _post_with_optional_token(
        GIGA_API_URL,
        headers_base=headers,
        access_token=access_token,
        json_payload=payload,
        timeout=120,
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

def ocr_instruction_via_rest(
    image_path: str,
    access_token: str | None,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """
    Отправляем в GigaChat-Pro изображение + промпт
    и получаем подробное текстовое описание инструкции.[web:67][web:69]
    """
    # 1. Загружаем изображение в файловое хранилище GigaChat и получаем file_id
    file_id = upload_image_to_files(image_path, access_token)

    # 2. Строим payload строго по схеме из readme_gigachat_api.md:
    #    model + messages[ {role, content, attachments: [file_id]} ]
    payload = {
        "model": model or VISION_MODEL,
        "temperature": float(temperature) if temperature is not None else VISION_TEMPERATURE,
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
					"Если приведен скриншот интерфейса, не приводи дословно содержимое, просто используй в инструкции как пояснение"
					"например: на скриншоте приведен пример как перейти в нужный раздел"
                ),
                "attachments": [file_id],
            },
        ],
    }

    headers = {
        "Content-Type": "application/json",
    }

    resp = _post_with_optional_token(
        GIGA_API_URL,
        headers_base=headers,
        access_token=access_token,
        json_payload=payload,
        timeout=120,
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
