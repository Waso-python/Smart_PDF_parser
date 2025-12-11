## Smart PDF parser (GigaChat, RAG‑friendly)

Скрипт для промышленной обработки PDF‑памяток по работе в АС:

- разбивает PDF на страницы;
- извлекает текстовый слой и делает скриншот каждой страницы;
- через GigaChat распознаёт инструкцию по скриншоту и объединяет её с текстовым слоем;
- собирает:
  - покомпонентные инструкции по страницам;
  - итоговый документ по всему PDF;
  - инкрементальный документ, где каждая новая страница уточняет уже накопленный контекст;
- помечает каждый смысловой элемент тегом источника `[SOURCE: page XXX]` для удобной привязки в RAG.

### Структура проекта

- `img_parse.py` — низкоуровневая работа с NGW и GigaChat (получение токена, REST‑вызовы, учёт токенов).
- `process_pamphlets.py` — основной пайплайн обработки PDF:
  - Этап 1: разбор PDF на страницы (`page_XXX/page.txt`, `page_XXX/page.jpg`);
  - Этап 2: для каждой страницы распознавание скриншота и объединение с текстовым слоем;
  - Этап 3: сборка независимых инструкций по страницам в `instructions_merged.md`;
  - Этап 4: инкрементальное накопление смысла по страницам с тегами `[SOURCE: page XXX]` в `instructions_incremental.md`;
  - в конце печатает суммарное потребление токенов (`prompt`, `completion`, `total`) за запуск.
- `requirements.txt` — минимальный набор зависимостей.
- `example_env.txt` — пример содержимого `.env` (боевой `.env` в Git **не коммитим**).
- `pdfs/` — каталог для входных PDF (игнорируется Git, создаёте сами).
- `out/` — каталог для результатов (игнорируется Git, создаётся скриптом).

### Установка

```bash
git clone git@github.com:Waso-python/Smart_PDF_parser.git
cd Smart_PDF_parser

python -m venv venv
venv\Scripts\activate  # Windows
# или source venv/bin/activate для Linux/macOS

pip install -r requirements.txt
```

Создайте `.env` на основе `example_env.txt` и заполните:

- `GIGA_ACCESS_KEY` — авторизационный ключ для NGW (строка `Basic ...`);
- `GIGA_CHAT_SCOPE` — обычно `GIGACHAT_API_CORP`;
- `GIGA_NGW_URL`, `GIGA_CHAT_COMPLETIONS_URL`, `GIGA_CHAT_FILES_URL` — при необходимости переопределите под свой контур;
- `GIGA_TEXT_MODEL`, `GIGA_VISION_MODEL` — названия используемых моделей GigaChat.

### Запуск пайплайна

1. Поместите входные PDF‑файлы в каталог `pdfs/` (например, `pdfs/manual.pdf`).
2. Запустите:

```bash
python process_pamphlets.py --pdf-dir pdfs --out-dir out
```

Аргументы:

- `--pdf-dir` — каталог с исходными PDF (по умолчанию `pdfs`);
- `--out-dir` — каталог для результатов (по умолчанию `out`).

В результате для каждого PDF `X.pdf` появится каталог `out/X/` со следующими файлами:

- `page_001/page.txt` — текстовый слой страницы 1;
- `page_001/page.jpg` — скриншот страницы 1;
- `page_001/instruction.txt` — итоговая инструкция по странице 1;
- `page_001/instruction_with_context.txt` — инструкция по страницам 1..1;
- `...`
- `instructions_merged.md` — конкатенация инструкций по всем страницам;
- `instructions_incremental.md` — единый документ с накопленным контекстом, где каждая смысловая строка имеет тег `[SOURCE: page XXX]`.

В конце работы скрипт выводит в терминал суммарное количество токенов, потраченных на все вызовы GigaChat за текущий запуск.


