import json
import os
import uuid
from pathlib import Path
from typing import Dict, Any, Tuple

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
)

from img_parse import get_creds, get_token_stats, ocr_instruction_via_rest, giga_free_answer
from process_pamphlets import stage4_build_incremental_context
from generate_faq import generate_faq_for_pages, _build_doc_context


load_dotenv()

# По умолчанию складываем результаты Web UI в out/web/, чтобы было видно рядом с CLI-пайплайном.
APP_DATA_DIR = Path(os.getenv("WEB_DATA_DIR", "out/web")).resolve()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _doc_dir(doc_id: str) -> Path:
    return APP_DATA_DIR / doc_id


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


def _process_page(doc_id: str, page_num: int) -> None:
    meta = _load_meta(doc_id)
    model = meta.get("model") or os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")
    temperature = float(meta.get("temperature", 0.01))

    access_token = _ensure_access_token()
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


def _generate_faq_for_page(doc_id: str, page_num: int) -> None:
    meta = _load_meta(doc_id)
    model = meta.get("model") or os.getenv("GIGA_TEXT_MODEL", "GigaChat-2-Pro")
    temperature = float(meta.get("temperature", 0.01))

    access_token = _ensure_access_token()
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
    <h3>Загрузить памятку (PDF)</h3>
    <form action="{{ url_for('upload') }}" method="post" enctype="multipart/form-data">
      <label>PDF файл</label>
      <input type="file" name="pdf" accept="application/pdf" required>

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
      <table>
        <thead><tr><th>Памятка</th><th>Страниц</th><th>Токены (total)</th><th></th></tr></thead>
        <tbody>
        {% for d in docs %}
          <tr>
            <td><strong>{{ d['pamphlet_name'] }}</strong><div class="muted">{{ d['doc_id'] }}</div></td>
            <td>{{ d.get('pages', '?') }}</td>
            <td>{{ d.get('tokens', {}).get('total_tokens', 0) }}</td>
            <td><a href="{{ url_for('doc', doc_id=d['doc_id']) }}">Открыть</a></td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
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
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        abort(400, "Нужен PDF файл.")

    model = (request.form.get("model") or "GigaChat-2-Pro").strip()
    temperature = float(request.form.get("temperature") or 0.01)

    doc_id = str(uuid.uuid4())
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

    return redirect(url_for("doc", doc_id=doc_id))


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


def main():
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()


