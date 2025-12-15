import json
import os
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple
from io import BytesIO

import fitz  # PyMuPDF
from dotenv import load_dotenv
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    send_file,
    abort,
    render_template_string,
    jsonify,
)

from img_parse import get_creds, get_token_stats, ocr_instruction_via_rest, giga_free_answer
from process_pamphlets import stage4_build_incremental_context
from generate_faq import generate_faq_for_pages, _build_doc_context
from openpyxl import Workbook


load_dotenv()

# По умолчанию складываем результаты Web UI в out/web/, чтобы было видно рядом с CLI-пайплайном.
APP_DATA_DIR = Path(os.getenv("WEB_DATA_DIR", "out/web")).resolve()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = (APP_DATA_DIR / "_jobs").resolve()
JOBS_DIR.mkdir(parents=True, exist_ok=True)

JOBS_LOCK = threading.Lock()
JOB_THREADS: dict[str, threading.Thread] = {}


def _doc_dir(doc_id: str) -> Path:
    return APP_DATA_DIR / doc_id


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _job_load(job_id: str) -> Dict[str, Any]:
    p = _job_path(job_id)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _job_save(job: Dict[str, Any]) -> None:
    job_id = str(job.get("job_id", ""))
    if not job_id:
        return
    p = _job_path(job_id)
    # На другой машине/в контейнере каталог может отсутствовать (или быть удалён во время работы).
    # Гарантируем наличие папки перед записью.
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def _job_update(job_id: str, **fields: Any) -> Dict[str, Any]:
    with JOBS_LOCK:
        job = _job_load(job_id) or {"job_id": job_id}
        job.update(fields)
        _job_save(job)
        return job


def _new_job(job_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "type": job_type,
        "status": "running",  # running|done|error
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "progress": {"done": 0, "total": 0},
        "current": {"doc_id": None, "page": None},
        "payload": payload,
        "error": None,
    }
    _job_save(job)
    return job


def _job_set_progress(job_id: str, done: int, total: int, doc_id: str | None = None, page: int | None = None) -> None:
    _job_update(
        job_id,
        updated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        progress={"done": int(done), "total": int(total)},
        current={"doc_id": doc_id, "page": page},
    )


def _job_finish(job_id: str) -> None:
    _job_update(
        job_id,
        status="done",
        updated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        current={"doc_id": None, "page": None},
    )


def _job_fail(job_id: str, error: str) -> None:
    _job_update(
        job_id,
        status="error",
        error=str(error),
        updated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        current={"doc_id": None, "page": None},
    )

def _meta_path(doc_id: str) -> Path:
    return _doc_dir(doc_id) / "meta.json"


def _load_meta(doc_id: str) -> Dict[str, Any]:
    p = _meta_path(doc_id)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_meta(doc_id: str, meta: Dict[str, Any]) -> None:
    p = _meta_path(doc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _token_delta(before: Dict[str, int], after: Dict[str, int]) -> Dict[str, int]:
    return {
        "prompt_tokens": after.get("prompt_tokens", 0) - before.get("prompt_tokens", 0),
        "completion_tokens": after.get("completion_tokens", 0)
        - before.get("completion_tokens", 0),
        "total_tokens": after.get("total_tokens", 0) - before.get("total_tokens", 0),
    }


def _add_tokens(meta: Dict[str, Any], delta: Dict[str, int]) -> None:
    cur = meta.get("tokens") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        cur[k] = int(cur.get(k, 0)) + int(delta.get(k, 0))
    meta["tokens"] = cur


def _extract_pages(pdf_path: Path, out_dir: Path, dpi: int = 150) -> int:
    doc = fitz.open(pdf_path)
    total = doc.page_count
    for i in range(total):
        page = doc.load_page(i)
        page_num = i + 1
        page_dir = out_dir / f"page_{page_num:03d}"
        page_dir.mkdir(parents=True, exist_ok=True)

        text = page.get_text("text")
        (page_dir / "page.txt").write_text(text, encoding="utf-8")

        pix = page.get_pixmap(dpi=dpi)
        pix.save(str(page_dir / "page.jpg"))
    return total


def _page_dir(doc_id: str, page_num: int) -> Path:
    return _doc_dir(doc_id) / f"page_{page_num:03d}"


def _ensure_access_token() -> str:
    creds = get_creds()
    token = creds.get("access_token")
    if not token:
        raise RuntimeError(f"Токен не получен от NGW. Ответ: {creds}")
    return token


def _process_page(doc_id: str, page_num: int, access_token: str | None = None) -> None:
    meta = _load_meta(doc_id)
    model = meta.get("model") or os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")
    temperature = float(meta.get("temperature", 0.01))

    access_token = access_token or _ensure_access_token()
    page_dir = _page_dir(doc_id, page_num)
    img_path = page_dir / "page.jpg"
    text_path = page_dir / "page.txt"
    if not img_path.exists() or not text_path.exists():
        raise FileNotFoundError("Не найдены файлы страницы (page.jpg/page.txt).")

    before = get_token_stats()

    # OCR по изображению
    ocr_text = ocr_instruction_via_rest(
        str(img_path),
        access_token,
        model=model,
        temperature=temperature,
    )
    (page_dir / "ocr.txt").write_text(ocr_text, encoding="utf-8")

    # Merge: текстовый слой + OCR → instruction.txt
    text_layer = text_path.read_text(encoding="utf-8")
    merge_question = (
        "У тебя есть две версии ОДНОЙ И ТОЙ ЖЕ страницы инструкции по работе в АС.\n\n"
        "Текстовый слой страницы (из PDF):\n"
        "----------------------------------------\n"
        f"{text_layer}\n"
        "----------------------------------------\n\n"
        "Текст, полученный по скриншоту страницы:\n"
        "----------------------------------------\n"
        f"{ocr_text}\n"
        "----------------------------------------\n\n"
        "Сформируй одну целостную инструкцию по этой странице.\n"
        "Строгие правила: не придумывай ничего, чего нет в исходных текстах. Убирай повторы.\n"
    )
    sys_prompt_merge = (
        "Ты опытный методолог. Объединяй версии одной страницы инструкции строго без домыслов."
    )
    instruction = giga_free_answer(
        question=merge_question,
        access_token=access_token,
        sys_prompt=sys_prompt_merge,
        model=model,
        temperature=temperature,
    )
    (page_dir / "instruction.txt").write_text(instruction, encoding="utf-8")

    # Обновляем инкрементальный контекст (только по тексту instruction.txt)
    stage4_build_incremental_context(
        pdf_dir=_doc_dir(doc_id),
        access_token=access_token,
        model=model,
        temperature=temperature,
    )

    after = get_token_stats()
    delta = _token_delta(before, after)
    _add_tokens(meta, delta)
    meta["last_op"] = {"type": "process_page", "page": page_num, "token_delta": delta}
    _save_meta(doc_id, meta)


def _generate_faq_for_page(doc_id: str, page_num: int, access_token: str | None = None) -> None:
    meta = _load_meta(doc_id)
    model = meta.get("model") or os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")
    temperature = float(meta.get("temperature", 0.01))

    access_token = access_token or _ensure_access_token()
    page_dir = _page_dir(doc_id, page_num)
    instr_path = page_dir / "instruction.txt"
    if not instr_path.exists():
        raise FileNotFoundError("Сначала выполните обработку страницы (instruction.txt не найден).")

    # Контекст документа: берём инкрементальный или merged, если есть
    inc_path = _doc_dir(doc_id) / "instructions_incremental.md"
    if inc_path.exists():
        doc_text = inc_path.read_text(encoding="utf-8")
    else:
        doc_text = instr_path.read_text(encoding="utf-8")

    doc_ctx = _build_doc_context(doc_text, max_chars=12000)
    page_text = instr_path.read_text(encoding="utf-8")

    before = get_token_stats()
    faq_md = generate_faq_for_pages(
        pages=[(page_num, page_text)],
        full_doc_context=doc_ctx,
        access_token=access_token,
        pamphlet_name=str(meta.get("pamphlet_name", meta.get("filename", "Памятка"))),
        output_tokens=10000,
    )
    after = get_token_stats()
    delta = _token_delta(before, after)

    (page_dir / "faq.md").write_text(faq_md, encoding="utf-8")

    _add_tokens(meta, delta)
    meta["last_op"] = {"type": "faq_page", "page": page_num, "token_delta": delta}
    _save_meta(doc_id, meta)

def _process_all_pages(doc_id: str) -> tuple[int, int]:
    """
    Массовая обработка: OCR+Merge+контекст для всех страниц документа.
    Возвращает (processed, total_pages).
    """
    meta = _load_meta(doc_id)
    total = int(meta.get("pages", 0) or 0)
    if total <= 0:
        return 0, 0
    token = _ensure_access_token()
    processed = 0
    for p in range(1, total + 1):
        _process_page(doc_id, p, access_token=token)
        processed += 1
    return processed, total


def _faq_all_pages(doc_id: str) -> tuple[int, int]:
    """
    Массовая генерация FAQ для всех страниц, где уже создан instruction.txt.
    Возвращает (generated, total_pages).
    """
    meta = _load_meta(doc_id)
    total = int(meta.get("pages", 0) or 0)
    if total <= 0:
        return 0, 0
    token = _ensure_access_token()
    generated = 0
    for p in range(1, total + 1):
        pd = _page_dir(doc_id, p)
        if not (pd / "instruction.txt").exists():
            continue
        _generate_faq_for_page(doc_id, p, access_token=token)
        generated += 1
    return generated, total

def _job_worker_process_docs(job_id: str, doc_ids: list[str]) -> None:
    """
    Job: обработать все страницы для списка документов.
    """
    try:
        token = _ensure_access_token()
        # total = сумма страниц документов
        total = 0
        for did in doc_ids:
            meta = _load_meta(did)
            total += int(meta.get("pages", 0) or 0)
        done = 0
        _job_set_progress(job_id, done=done, total=total)

        for did in doc_ids:
            meta = _load_meta(did)
            pages = int(meta.get("pages", 0) or 0)
            for p in range(1, pages + 1):
                _job_set_progress(job_id, done=done, total=total, doc_id=did, page=p)
                _process_page(did, p, access_token=token)
                done += 1

        _job_set_progress(job_id, done=done, total=total)
        _job_finish(job_id)
    except Exception as e:
        _job_fail(job_id, str(e))


def _job_worker_faq_docs(job_id: str, doc_ids: list[str]) -> None:
    """
    Job: сгенерировать FAQ для всех страниц документов, где уже есть instruction.txt.
    """
    try:
        token = _ensure_access_token()
        # total = кол-во страниц с instruction.txt
        total = 0
        page_targets: list[tuple[str, int]] = []
        for did in doc_ids:
            meta = _load_meta(did)
            pages = int(meta.get("pages", 0) or 0)
            for p in range(1, pages + 1):
                pd = _page_dir(did, p)
                if (pd / "instruction.txt").exists():
                    total += 1
                    page_targets.append((did, p))
        done = 0
        _job_set_progress(job_id, done=done, total=total)

        for did, p in page_targets:
            _job_set_progress(job_id, done=done, total=total, doc_id=did, page=p)
            _generate_faq_for_page(did, p, access_token=token)
            done += 1

        _job_set_progress(job_id, done=done, total=total)
        _job_finish(job_id)
    except Exception as e:
        _job_fail(job_id, str(e))


def _start_job_thread(job_id: str, target, *args) -> None:
    t = threading.Thread(target=target, args=(job_id, *args), daemon=True)
    with JOBS_LOCK:
        JOB_THREADS[job_id] = t
    t.start()


def _build_instruction_export_md(doc_id: str) -> str:
    """
    Собираем итоговую инструкцию для выгрузки:
    - если есть instructions_incremental.md — отдаём его;
    - иначе — склеиваем instruction.txt по страницам с заголовками.
    """
    ddir = _doc_dir(doc_id)
    inc = ddir / "instructions_incremental.md"
    if inc.exists():
        return inc.read_text(encoding="utf-8")

    meta = _load_meta(doc_id)
    pages = int(meta.get("pages", 0) or 0)
    chunks = []
    for p in range(1, pages + 1):
        pd = _page_dir(doc_id, p)
        ip = pd / "instruction.txt"
        if not ip.exists():
            continue
        txt = ip.read_text(encoding="utf-8").strip()
        if not txt:
            continue
        chunks.append(f"## Страница {p:03d}\n\n{txt}\n")
    return "\n\n".join(chunks).strip() + "\n"


def _build_faq_export_md(doc_id: str) -> str:
    """
    Склеиваем FAQ по всем страницам (page_XXX/faq.md).
    """
    meta = _load_meta(doc_id)
    pages = int(meta.get("pages", 0) or 0)
    chunks = []
    for p in range(1, pages + 1):
        pd = _page_dir(doc_id, p)
        fp = pd / "faq.md"
        if not fp.exists():
            continue
        txt = fp.read_text(encoding="utf-8").strip()
        if not txt:
            continue
        chunks.append(txt)
    return "\n\n".join(chunks).strip() + "\n"

def _parse_faq_md_to_rows(md: str) -> list[dict]:
    """
    Поддерживаем формат:
      ВОПРОС: ...
      ОТВЕТ/ИНСТРУКЦИЯ: ...
      [SOURCE - "..."]
    """
    import re

    block_re = re.compile(
        r"ВОПРОС:\s*(?P<q>.*?)(?:\r?\n)+"
        r"(?:ОТВЕТ|ИНСТРУКЦИЯ):\s*(?P<a>.*?)(?:\r?\n)+"
        r"\[SOURCE\s*-\s*\"(?P<s>.*?)\"\]\s*",
        re.DOTALL | re.IGNORECASE,
    )
    rows = []
    for m in block_re.finditer(md.strip()):
        rows.append(
            {
                "question": m.group("q").strip(),
                "answer": m.group("a").strip(),
                "source": m.group("s").strip(),
            }
        )
    return rows


def _rows_to_xlsx_bytes(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "FAQ"
    ws.append(["Вопрос", "Ответ", "Источник"])
    for r in rows:
        ws.append([r.get("question", ""), r.get("answer", ""), r.get("source", "")])
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 90
    ws.column_dimensions["C"].width = 40

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _missing_pages(doc_id: str, kind: str) -> list[int]:
    """
    kind:
      - "instruction": проверяем наличие page_XXX/instruction.txt
      - "faq": проверяем наличие page_XXX/faq.md
    """
    meta = _load_meta(doc_id)
    pages = int(meta.get("pages", 0) or 0)
    missing: list[int] = []
    filename = "instruction.txt" if kind == "instruction" else "faq.md"
    for p in range(1, pages + 1):
        pd = _page_dir(doc_id, p)
        if not (pd / filename).exists():
            missing.append(p)
    return missing


app = Flask(__name__)
app.secret_key = os.getenv("WEB_SECRET_KEY", "dev-secret")


INDEX_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Smart PDF Parser — Web UI</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    label { display: block; font-weight: 600; margin-top: 8px; }
    input[type="text"], input[type="number"] { width: 320px; padding: 8px; border: 1px solid #d1d5db; border-radius: 8px; }
    input[type="file"] { margin-top: 8px; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; background: #111827; color: #fff; cursor: pointer; }
    a { color: #1d4ed8; text-decoration: none; }
    .muted { color: #6b7280; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align: left; }
  </style>
</head>
<body>
  <h2>Smart PDF Parser — Web UI</h2>

  <div class="card">
    <h3>Загрузить памятки (PDF)</h3>
    <form action="{{ url_for('upload') }}" method="post" enctype="multipart/form-data">
      <label>PDF файлы</label>
      <input type="file" name="pdfs" accept="application/pdf" multiple required>

      <div class="row">
        <div>
          <label>Модель</label>
          <input type="text" name="model" value="GigaChat-2-Pro">
        </div>
        <div>
          <label>Температура</label>
          <input type="number" step="0.01" min="0" max="2" name="temperature" value="0.01">
        </div>
      </div>

      <div style="margin-top: 12px;">
        <button type="submit">Загрузить и разобрать страницы</button>
      </div>
      <p class="muted">После загрузки: создаются page_XXX/page.txt и page_XXX/page.jpg. Обработка страниц и FAQ — по кнопкам.</p>
    </form>
  </div>

  <div class="card">
    <h3>Документы</h3>
    {% if docs %}
      <form action="{{ url_for('batch_process_docs') }}" method="post">
        <div class="row" style="align-items:center; margin-bottom: 8px;">
          <button type="submit" name="action" value="process">Обработать выбранные документы (все страницы)</button>
          <button type="submit" name="action" value="faq">Сгенерировать FAQ для выбранных документов</button>
          <span class="muted">Внимание: массовые операции могут выполняться долго.</span>
        </div>
        <table>
          <thead><tr><th></th><th>Памятка</th><th>Страниц</th><th>Токены (total)</th><th></th></tr></thead>
          <tbody>
          {% for d in docs %}
            <tr>
              <td><input type="checkbox" name="doc_id" value="{{ d['doc_id'] }}"></td>
              <td><strong>{{ d['pamphlet_name'] }}</strong><div class="muted">{{ d['doc_id'] }}</div></td>
              <td>{{ d.get('pages', '?') }}</td>
              <td>{{ d.get('tokens', {}).get('total_tokens', 0) }}</td>
              <td><a href="{{ url_for('doc', doc_id=d['doc_id']) }}">Открыть</a></td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </form>
    {% else %}
      <p class="muted">Пока нет загруженных документов.</p>
    {% endif %}
  </div>
</body>
</html>
"""


DOC_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{{ meta.get('pamphlet_name','Документ') }} — Smart PDF Parser</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    a { color: #1d4ed8; text-decoration: none; }
    .muted { color: #6b7280; }
    .row { display:flex; gap: 16px; flex-wrap: wrap; align-items: center; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; background: #111827; color: #fff; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid #eee; text-align: left; }
    code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <div class="row" style="justify-content: space-between;">
    <h2>{{ meta.get('pamphlet_name','Документ') }}</h2>
    <a href="{{ url_for('index') }}">← назад</a>
  </div>

  <div class="card">
    <div class="row">
      <div><strong>ID:</strong> <code>{{ doc_id }}</code></div>
      <div><strong>Модель:</strong> <code>{{ meta.get('model') }}</code></div>
      <div><strong>Температура:</strong> <code>{{ meta.get('temperature') }}</code></div>
      <div><strong>Токены total:</strong> <code>{{ meta.get('tokens', {}).get('total_tokens', 0) }}</code></div>
    </div>
    <p class="muted"><strong>Каталог документа:</strong> <code>{{ meta.get('storage_dir','') }}</code></p>
    <div class="row">
      <a href="{{ url_for('download_instruction', doc_id=doc_id) }}">Скачать итоговую инструкцию (.md)</a>
      <a href="{{ url_for('download_faq_xlsx', doc_id=doc_id) }}">Скачать FAQ по всем страницам (.xlsx)</a>
    </div>
    <div class="row" style="margin-top: 10px;">
      <form action="{{ url_for('doc_process_all', doc_id=doc_id) }}" method="post">
        <button type="submit">Обработать все страницы</button>
      </form>
      <form action="{{ url_for('doc_faq_all', doc_id=doc_id) }}" method="post">
        <button type="submit">FAQ по всем страницам</button>
      </form>
    </div>
    {% if meta.get('last_op') %}
      <p class="muted">Последняя операция: {{ meta['last_op']['type'] }} (стр. {{ meta['last_op'].get('page','-') }}), delta total={{ meta['last_op']['token_delta']['total_tokens'] }}</p>
    {% endif %}
  </div>

  <div class="card">
    <h3>Страницы</h3>
    <table>
      <thead><tr><th>Страница</th><th>Файлы</th><th></th></tr></thead>
      <tbody>
      {% for p in pages %}
        <tr>
          <td>{{ "%03d"|format(p) }}</td>
          <td class="muted">
            {% set pd = page_dirs[p] %}
            {{ "img" if pd['has_img'] else "-" }} /
            {{ "txt" if pd['has_txt'] else "-" }} /
            {{ "instr" if pd['has_instr'] else "-" }} /
            {{ "faq" if pd['has_faq'] else "-" }}
          </td>
          <td><a href="{{ url_for('page', doc_id=doc_id, page_num=p) }}">Открыть</a></td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</body>
</html>
"""

JOB_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Задание {{ job.get('job_id','') }} — Smart PDF Parser</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .muted { color: #6b7280; }
    .bar { width: 100%; height: 16px; background: #e5e7eb; border-radius: 10px; overflow: hidden; }
    .fill { height: 16px; background: #111827; width: 0%; }
    code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
    a { color: #1d4ed8; text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <div style="display:flex; justify-content: space-between; gap: 16px; align-items:center;">
      <div>
        <h2 style="margin:0;">Задание</h2>
        <div class="muted"><code>{{ job.get('job_id','') }}</code></div>
      </div>
      <div>
        <a href="{{ url_for('index') }}">← на главную</a>
      </div>
    </div>

    <p class="muted">
      Тип: <code>{{ job.get('type','') }}</code> ·
      Статус: <code id="st">{{ job.get('status','') }}</code>
    </p>

    <div class="bar"><div class="fill" id="fill"></div></div>
    <p style="margin-top: 8px;">
      <strong id="pct">0%</strong>
      <span class="muted">(<span id="done">0</span>/<span id="total">0</span>)</span>
    </p>
    <p class="muted" id="cur"></p>
    <p style="color:#b91c1c;" id="err"></p>

    <p class="muted">
      Обновляется автоматически. Если закрыть вкладку, прогресс всё равно сохранится, а страницу можно открыть снова по ссылке.
    </p>
  </div>

  <script>
    async function tick() {
      const r = await fetch("{{ url_for('job_status', job_id=job.get('job_id','')) }}", { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const done = (j.progress && j.progress.done) ? j.progress.done : 0;
      const total = (j.progress && j.progress.total) ? j.progress.total : 0;
      const pct = total > 0 ? Math.floor((done * 100) / total) : 0;

      document.getElementById("st").textContent = j.status || "";
      document.getElementById("done").textContent = done;
      document.getElementById("total").textContent = total;
      document.getElementById("pct").textContent = pct + "%";
      document.getElementById("fill").style.width = pct + "%";

      const cdoc = j.current && j.current.doc_id ? j.current.doc_id : "";
      const cpage = j.current && j.current.page ? j.current.page : "";
      document.getElementById("cur").textContent =
        (cdoc ? ("Документ: " + cdoc + (cpage ? (", страница: " + String(cpage).padStart(3,'0')) : "")) : "");

      document.getElementById("err").textContent = j.error || "";

      if (j.status === "running") {
        setTimeout(tick, 1000);
      }
    }
    tick();
  </script>
</body>
</html>
"""

WARNING_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Предупреждение — неполная обработка</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .warn { color: #b45309; font-weight: 700; }
    code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
    a.btn { display:inline-block; padding: 10px 14px; border-radius: 10px; text-decoration:none; margin-right: 10px; }
    a.primary { background:#111827; color:#fff; }
    a.secondary { background:#e5e7eb; color:#111827; }
    ul { margin: 8px 0 0 18px; }
  </style>
</head>
<body>
  <div class="card">
    <h2 class="warn">Предупреждение</h2>
    <p>Не по всем страницам выполнен разбор для выгрузки: <code>{{ kind_label }}</code>.</p>
    <p>Документ: <code>{{ meta.get('pamphlet_name','document') }}</code>, страниц: <code>{{ meta.get('pages') }}</code></p>
    <p>Отсутствуют страницы:</p>
    <ul>
      {% for p in missing %}
        <li>{{ "%03d"|format(p) }}</li>
      {% endfor %}
    </ul>
  </div>
  <div class="card">
    <a class="btn primary" href="{{ force_url }}">Скачать всё равно</a>
    <a class="btn secondary" href="{{ back_url }}">Вернуться к документу</a>
  </div>
</body>
</html>
"""


PAGE_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{{ meta.get('pamphlet_name','Документ') }} — стр {{ "%03d"|format(page_num) }}</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .row { display:flex; gap: 16px; flex-wrap: wrap; }
    .col { flex: 1; min-width: 360px; }
    img { max-width: 100%; border-radius: 10px; border: 1px solid #e5e7eb; }
    pre { white-space: pre-wrap; background: #0b1020; color: #e5e7eb; padding: 12px; border-radius: 10px; overflow:auto; }
    .muted { color: #6b7280; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; background: #111827; color: #fff; cursor: pointer; }
    a { color: #1d4ed8; text-decoration: none; }
    .bar { display:flex; justify-content: space-between; align-items:center; gap: 12px; flex-wrap: wrap; }
  </style>
</head>
<body>
  <div class="bar">
    <h2>{{ meta.get('pamphlet_name','Документ') }} — страница {{ "%03d"|format(page_num) }}</h2>
    <a href="{{ url_for('doc', doc_id=doc_id) }}">← к документу</a>
  </div>

  <div class="card">
    <div class="bar">
      <div class="muted">Модель: {{ meta.get('model') }} | t={{ meta.get('temperature') }} | tokens total={{ meta.get('tokens', {}).get('total_tokens', 0) }}</div>
      <div class="row">
        <form action="{{ url_for('process_page', doc_id=doc_id, page_num=page_num) }}" method="post">
          <button type="submit">Обработать страницу (OCR+Merge + контекст)</button>
        </form>
        <form action="{{ url_for('faq_page', doc_id=doc_id, page_num=page_num) }}" method="post">
          <button type="submit" {% if not has_instruction %}disabled{% endif %}>Сгенерировать FAQ</button>
        </form>
      </div>
    </div>
    <p class="muted"><strong>Каталог страницы:</strong> <code>{{ page_dir }}</code></p>
    {% if meta.get('last_error') %}
      <p style="color:#b91c1c;"><strong>Ошибка:</strong> {{ meta['last_error'] }}</p>
    {% endif %}
    {% if meta.get('last_op') %}
      <p class="muted">Последняя операция: {{ meta['last_op']['type'] }} (стр. {{ meta['last_op'].get('page','-') }}), delta total={{ meta['last_op']['token_delta']['total_tokens'] }}</p>
    {% endif %}
  </div>

  <div class="row">
    <div class="col card">
      <h3>Скриншот</h3>
      {% if has_img %}
        <img src="{{ url_for('page_image', doc_id=doc_id, page_num=page_num) }}" alt="page">
      {% else %}
        <p class="muted">Нет page.jpg</p>
      {% endif %}
    </div>

    <div class="col card">
      <h3>Текстовый слой (PDF)</h3>
      <pre>{{ page_text or "" }}</pre>
    </div>
  </div>

  <div class="row">
    <div class="col card">
      <h3>OCR (по скриншоту)</h3>
      {% if ocr_text %}
        <pre>{{ ocr_text }}</pre>
      {% else %}
        <p class="muted">Нет OCR для этой страницы. Нажмите «Обработать страницу».</p>
      {% endif %}
    </div>
    <div class="col card">
      <h3>Инструкция (merge)</h3>
      {% if instruction %}
        <pre>{{ instruction }}</pre>
      {% else %}
        <p class="muted">Нет инструкции для этой страницы. Нажмите «Обработать страницу».</p>
      {% endif %}
    </div>
  </div>

  <div class="card">
    <h3>FAQ</h3>
    {% if faq %}
      <pre>{{ faq }}</pre>
    {% else %}
      <p class="muted">FAQ ещё не сгенерирован. Нажмите «Сгенерировать FAQ» (после обработки страницы).</p>
    {% endif %}
  </div>
</body>
</html>
"""


@app.get("/")
def index():
    docs = []
    for p in sorted(APP_DATA_DIR.iterdir()) if APP_DATA_DIR.exists() else []:
        if not p.is_dir():
            continue
        doc_id = p.name
        meta = _load_meta(doc_id)
        if not meta:
            continue
        docs.append(
            {
                "doc_id": doc_id,
                "pamphlet_name": meta.get("pamphlet_name", meta.get("filename", doc_id)),
                "pages": meta.get("pages"),
                "tokens": meta.get("tokens", {}),
            }
        )
    return render_template_string(INDEX_HTML, docs=docs)


@app.post("/upload")
def upload():
    files = request.files.getlist("pdfs") or []
    files = [f for f in files if f and f.filename]
    if not files:
        abort(400, "Нужны PDF файлы.")

    model = (request.form.get("model") or "GigaChat-2-Pro").strip()
    temperature = float(request.form.get("temperature") or 0.01)

    first_doc_id: str | None = None
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        doc_id = str(uuid.uuid4())
        if first_doc_id is None:
            first_doc_id = doc_id

        ddir = _doc_dir(doc_id)
        ddir.mkdir(parents=True, exist_ok=True)

        pdf_path = ddir / "source.pdf"
        f.save(pdf_path)

        total_pages = _extract_pages(pdf_path, ddir, dpi=150)

        meta = {
            "doc_id": doc_id,
            "filename": f.filename,
            "pamphlet_name": Path(f.filename).stem,
            "pages": total_pages,
            "model": model,
            "temperature": temperature,
            "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "storage_dir": str(ddir),
        }
        _save_meta(doc_id, meta)

    return redirect(url_for("index") if first_doc_id is None else url_for("doc", doc_id=first_doc_id))


@app.get("/doc/<doc_id>")
def doc(doc_id: str):
    meta = _load_meta(doc_id)
    if not meta:
        abort(404)
    pages = list(range(1, int(meta.get("pages", 0)) + 1))
    page_dirs: Dict[int, Dict[str, bool]] = {}
    for p in pages:
        pd = _page_dir(doc_id, p)
        page_dirs[p] = {
            "has_img": (pd / "page.jpg").exists(),
            "has_txt": (pd / "page.txt").exists(),
            "has_instr": (pd / "instruction.txt").exists(),
            "has_faq": (pd / "faq.md").exists(),
        }
    return render_template_string(DOC_HTML, doc_id=doc_id, meta=meta, pages=pages, page_dirs=page_dirs)


@app.get("/doc/<doc_id>/page/<int:page_num>")
def page(doc_id: str, page_num: int):
    meta = _load_meta(doc_id)
    if not meta:
        abort(404)
    pd = _page_dir(doc_id, page_num)
    if not pd.exists():
        abort(404)

    page_text = (pd / "page.txt").read_text(encoding="utf-8") if (pd / "page.txt").exists() else ""
    ocr_text = (pd / "ocr.txt").read_text(encoding="utf-8") if (pd / "ocr.txt").exists() else ""
    instruction = (pd / "instruction.txt").read_text(encoding="utf-8") if (pd / "instruction.txt").exists() else ""
    faq = (pd / "faq.md").read_text(encoding="utf-8") if (pd / "faq.md").exists() else ""
    has_img = (pd / "page.jpg").exists()
    has_instruction = (pd / "instruction.txt").exists()

    return render_template_string(
        PAGE_HTML,
        doc_id=doc_id,
        page_num=page_num,
        meta=meta,
        has_img=has_img,
        has_instruction=has_instruction,
        page_dir=str(pd),
        page_text=page_text,
        ocr_text=ocr_text,
        instruction=instruction,
        faq=faq,
    )


@app.get("/doc/<doc_id>/page/<int:page_num>/image")
def page_image(doc_id: str, page_num: int):
    pd = _page_dir(doc_id, page_num)
    img = pd / "page.jpg"
    if not img.exists():
        abort(404)
    return send_file(img, mimetype="image/jpeg")


@app.get("/doc/<doc_id>/download/instruction")
def download_instruction(doc_id: str):
    meta = _load_meta(doc_id)
    if not meta:
        abort(404)
    missing = _missing_pages(doc_id, "instruction")
    if missing and request.args.get("force") != "1":
        return render_template_string(
            WARNING_HTML,
            meta=meta,
            missing=missing,
            kind_label="instruction.txt",
            force_url=url_for("download_instruction", doc_id=doc_id, force=1),
            back_url=url_for("doc", doc_id=doc_id),
        )
    md = _build_instruction_export_md(doc_id)
    filename = f"{meta.get('pamphlet_name','document')}_instruction.md"
    return send_file(
        BytesIO(md.encode("utf-8")),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


@app.get("/doc/<doc_id>/download/faq")
def download_faq(doc_id: str):
    meta = _load_meta(doc_id)
    if not meta:
        abort(404)
    missing = _missing_pages(doc_id, "faq")
    if missing and request.args.get("force") != "1":
        return render_template_string(
            WARNING_HTML,
            meta=meta,
            missing=missing,
            kind_label="faq.md",
            force_url=url_for("download_faq", doc_id=doc_id, force=1),
            back_url=url_for("doc", doc_id=doc_id),
        )
    md = _build_faq_export_md(doc_id)
    filename = f"{meta.get('pamphlet_name','document')}_faq.md"
    return send_file(
        BytesIO(md.encode("utf-8")),
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )

@app.get("/doc/<doc_id>/download/faq.xlsx")
def download_faq_xlsx(doc_id: str):
    meta = _load_meta(doc_id)
    if not meta:
        abort(404)
    missing = _missing_pages(doc_id, "faq")
    if missing and request.args.get("force") != "1":
        return render_template_string(
            WARNING_HTML,
            meta=meta,
            missing=missing,
            kind_label="faq.md",
            force_url=url_for("download_faq_xlsx", doc_id=doc_id, force=1),
            back_url=url_for("doc", doc_id=doc_id),
        )

    md = _build_faq_export_md(doc_id)
    rows = _parse_faq_md_to_rows(md)
    xlsx_bytes = _rows_to_xlsx_bytes(rows)
    filename = f"{meta.get('pamphlet_name','document')}_faq.xlsx"
    return send_file(
        BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )

@app.post("/doc/<doc_id>/page/<int:page_num>/process")
def process_page(doc_id: str, page_num: int):
    try:
        _process_page(doc_id, page_num)
        meta = _load_meta(doc_id)
        if meta.get("last_error"):
            meta.pop("last_error", None)
            _save_meta(doc_id, meta)
    except Exception as e:
        meta = _load_meta(doc_id)
        meta["last_error"] = str(e)
        _save_meta(doc_id, meta)
    return redirect(url_for("page", doc_id=doc_id, page_num=page_num))


@app.post("/doc/<doc_id>/page/<int:page_num>/faq")
def faq_page(doc_id: str, page_num: int):
    try:
        _generate_faq_for_page(doc_id, page_num)
        meta = _load_meta(doc_id)
        if meta.get("last_error"):
            meta.pop("last_error", None)
            _save_meta(doc_id, meta)
    except Exception as e:
        meta = _load_meta(doc_id)
        meta["last_error"] = str(e)
        _save_meta(doc_id, meta)
    return redirect(url_for("page", doc_id=doc_id, page_num=page_num))

@app.get("/job/<job_id>")
def job(job_id: str):
    j = _job_load(job_id)
    if not j:
        abort(404)
    return render_template_string(JOB_HTML, job=j)


@app.get("/job/<job_id>/status")
def job_status(job_id: str):
    j = _job_load(job_id)
    if not j:
        abort(404)
    return jsonify(j)


@app.post("/doc/<doc_id>/process_all")
def doc_process_all(doc_id: str):
    # запускаем в фоне и показываем прогресс
    j = _new_job("process_docs", {"doc_ids": [doc_id]})
    _start_job_thread(j["job_id"], _job_worker_process_docs, [doc_id])
    return redirect(url_for("job", job_id=j["job_id"]))


@app.post("/doc/<doc_id>/faq_all")
def doc_faq_all(doc_id: str):
    j = _new_job("faq_docs", {"doc_ids": [doc_id]})
    _start_job_thread(j["job_id"], _job_worker_faq_docs, [doc_id])
    return redirect(url_for("job", job_id=j["job_id"]))


@app.post("/batch/process_docs")
def batch_process_docs():
    doc_ids = request.form.getlist("doc_id")
    action = request.form.get("action") or "process"
    if not doc_ids:
        return redirect(url_for("index"))
    if action == "faq":
        j = _new_job("faq_docs", {"doc_ids": doc_ids})
        _start_job_thread(j["job_id"], _job_worker_faq_docs, doc_ids)
    else:
        j = _new_job("process_docs", {"doc_ids": doc_ids})
        _start_job_thread(j["job_id"], _job_worker_process_docs, doc_ids)
    return redirect(url_for("job", job_id=j["job_id"]))


def main():
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()


