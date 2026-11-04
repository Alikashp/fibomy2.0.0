# Fibonacci AI

Telegram-бот и REST API, которые по теме, тексту или документу пользователя собирают готовую презентацию: **редактируемый PPTX** с нативными диаграммами и **PDF**. Пользователь проходит короткий диалог (тип, тема, аудитория, язык, объём, материал), фоновый воркер строит план слайдов через LLM, подбирает вид каждого слайда, пишет текст, подгоняет его под макет без обрезки, рисует PPTX и конвертирует его в PDF.

Аудитория сейчас — студенты и преподаватели, дальше — корпоративные пользователи. К REST API подключён второй бот владельца с **живыми пользователями**, поэтому API меняется только обратно совместимо (`docs/API.md`).

---

## С чего начать

Порядок чтения для нового разработчика (около часа):

1. **Этот файл:** как устроен сервис, путь одного запроса, словарь терминов, запуск.
2. **`src/core/pipeline.py`, функция `generate_deck`.** Это всё ядро по порядку этапов: в начале файла — список этапов, в теле — пометки `# 1 INGEST`, `# 2 OUTLINE` и т. д.
3. **`src/core/models/request.py` и `src/core/models/deck.py`.** Это вход ядра (`DeckRequest`) и его главная структура данных (`DeckSpec`). Полное описание — `docs/design/04_CONTRACTS.md`.
4. **Один макет и одна тема:** `layouts/bullets/cards_grid.yaml` и `themes/business_slate.yaml`. Вид слайдов задают эти файлы, а не код.
5. **Запустить тесты и галерею** (раздел «Запуск»): галерея без ключей показывает все макеты во всех темах.

Дальше — по задаче:

| Нужно | Где |
|---|---|
| Что строим и зачем, требования | `docs/TZ.md` — главный документ, при расхождениях прав он; `docs/PRODUCT.md` |
| Как задуман новый движок | `docs/design/01…10` (архитектура — `03_ARCHITECTURE.md`, макеты — `05_LAYOUTS.md`, промпты — `06_PROMPTS.md`, тесты — `07_TESTING.md`) |
| Почему сделано так, а не иначе | `docs/DECISIONS.md`. В коде ссылки вида `D-071` ведут на запись там |
| Что сделано и что дальше | `docs/PROGRESS.md`, новые записи сверху |
| Контракт REST API | `docs/API.md` |
| Правила работы с репозиторием | `CLAUDE.md`, разделы «Правила» (написан для ИИ-ассистента, но правила общие) |

`docs/CURRENT_STATE.md` — снимок кода на 26.09.2026, до нового движка. Он исторический, текущее устройство описывает этот файл.

---

## Как устроено

Три сервиса Railway из одного Docker-образа. Какой процесс запускать, решает переменная `SERVICE_ROLE` (`src/start.py`).

```
 Пользователь Telegram              Второй бот (клиент API)
         │                                   │  POST /v1/decks
         ▼                                   ▼
 ┌───────────────┐                  ┌────────────────┐
 │ bot           │                  │ api (FastAPI)  │
 │ src/main.py   │                  │ src/api/app.py │
 └──────┬────────┘                  └───────┬────────┘
        │  строка decks (Postgres) + задача generate_deck_job (Redis, ARQ)
        └──────────────────┬────────────────┘
                           ▼
                 ┌──────────────────────┐
                 │ worker (ARQ)         │
                 │ src/worker.py        │──► core.pipeline.generate_deck ──► PPTX + PDF
                 └──────────┬───────────┘
                            │
       бот: файлы в чат     │     API: файлы в S3 или Redis на 1 ч, webhook клиенту,
       (src/bot/delivery)   │     клиент скачивает GET /v1/decks/{id}/files/{pptx|pdf}
```

Главное правило: **`src/core` не знает про Telegram и HTTP.** Бот и API только принимают запрос, создают строку `decks` и ставят задачу. Воркер вызывает ядро и отдаёт результат. Порядок этапов знает только `pipeline.py`, этапы друг друга не вызывают.

### Путь одного запроса (бот)

| Шаг | Что происходит | Где |
|---|---|---|
| 1 | Диалог: тип, тема, материал, сводка с кнопками «Сменить…» | `src/main.py` (обработчики), `src/dialog.py` (тексты и клавиатуры сводки) |
| 2 | «Сгенерировать»: материал кладётся во временное хранилище, создаётся строка `decks` (status `queued`), ставится задача | `main.generate_deck`, `src/generation/uploads.py`, `src/core/storage/decks.py` |
| 3 | Воркер берёт задачу, читает `DeckRequest` из `decks`, правит статусное сообщение в чате по этапам | `worker.generate_deck_job`, `src/bot/delivery.py` (`StatusMessage`) |
| 4 | Ядро собирает колоду (этапы — ниже) | `src/core/pipeline.py` |
| 5 | Результат пишется в `decks`, файлы уходят в чат, списывается генерация | `worker.generate_deck_job`, `bot/delivery.send_files`, `db/session.count_generation` |

Через API шаги 1–2 делает `POST /v1/decks` (`src/api/app.py: create_deck`), а на шаге 5 файлы кладутся в хранилище (`core/storage/files.py`) и клиенту уходит webhook (`api/webhook.py`).

### Этапы генерации (`src/core`)

| # | Этап | Что делает | Код | Выход |
|---|---|---|---|---|
| 1 | INGEST | Разбирает материал (docx, pdf, pptx, txt, текст): фрагменты текста и таблицы с числами | `core/ingest/` | `SourceDigest` |
| 2 | OUTLINE | LLM строит план: жанр, тезисы, по слайду — kind, роль, заголовок; код проверяет план; титул — тема без пожеланий | `core/planning/outline.py`, `title.py`, `datasets.py` | список `PlannedSlide` |
| 3 | SELECT | Код выбирает вариант макета для каждого слайда по вместимости и разнообразию (детерминированно по `seed`) | `core/selection/select.py` | вариант на слайд |
| 4 | IMAGES | ИИ-картинки SiliconFlow, параллельно с CONTENT; не успела — вариант без картинки | `core/images/` | файлы картинок |
| 5 | CONTENT | LLM пишет текст каждого слайда по схеме его kind, слайды параллельно | `core/content/fill.py` | `Slide.content` |
| 6 | Проверки текста и чисел | Мусор генерации (иероглифы, склейки, английские вставки); числа не из источника — перегенерация слайда | `core/checks/text.py`, `core/checks/facts.py` | исправленный `DeckSpec` |
| 7 | Типографика | Неразрывные диапазоны, единицы, разряды; десятичная запятая по языку | `core/text/typography.py` | — |
| 8 | FIT | Подгонка под макет без обрезки: кегль → другой вариант → «сократи до N знаков» → два слайда; заполненность не ниже 40% | `core/fitting/fitter.py`, `resolve.py`, `fill.py`, `metrics.py` | `Slide.fit` |
| 9 | RENDER | `DeckSpec` → PPTX (python-pptx): фигуры, текст, нативные диаграммы, иконки; финальная проверка вёрстки готового файла | `core/render/pptx/`, `core/checks/layout.py` | PPTX |
| 10 | CONVERT | PPTX → PDF через LibreOffice | `core/render/pdf/convert.py` | PDF |

Общие части ядра:
- `core/llm/` — клиент LLM (structured outputs, повторы, 429, учёт токенов и стоимости);
- `core/models/` — все структуры данных;
- `core/storage/` — строка `decks`, файлы колод, прогресс.

**Деградация вместо падения.** У каждого этапа есть запасной путь: картинка не успела — вариант без картинки; CONTENT не ответил — слайд-утверждение из плана; и т. д. Каждый такой случай записывается в `DeckSpec.degradations`. Колода падает целиком (`DeckError` с кодом) только тогда, когда отдать нечего.

---

## Карта репозитория

```
src/
  core/            новый движок (PPTX + PDF): этапы выше; не зависит от бота, API и БД (кроме core/storage)
  main.py          Telegram-бот: обработчики диалога, профиль, тарифы, оплата (aiogram 3)
  dialog.py        тексты и клавиатуры сводки перед генерацией
  bot/             delivery.py — выдача файлов и статус; admin.py — /apikey для владельца; text_join.py — склейка длинного текста
  api/             REST API v1: app.py — эндпоинты; store.py — клиенты и их колоды; limits, webhook, keys (CLI ключей)
  worker.py        задачи ARQ: generate_deck_job (новый движок), webhook, уведомления, старый движок
  db/              SQLAlchemy-модели (models.py), подключение и функции пользователей (session.py), миграции Alembic
  config.py        все настройки из переменных окружения; полный список — .env.example
  start.py         точка входа образа: bot | worker | api по SERVICE_ROLE
  logging_setup.py JSON-логи, deck_id в каждой строке, замер этапов

  generation/, schemas/, layouts/zones.py, templates/
                   СТАРЫЙ движок (HTML → PDF): только питч-дек, удаляется после его перевода
                   на новый движок. Новый код сюда не пишем.

layouts/<kind>/<variant>.yaml   макеты слайдов: элементы, рамки, вместимость, когда вариант применим (25 файлов)
themes/<id>.yaml                цветовые темы: палитра, шрифты, декор (4 темы)
prompts/v2/                     промпты нового движка (README внутри); prompts/models.yaml — параметры и цены моделей
prompts/*.txt|yaml              промпты старого движка
fonts/                          метрики шрифтов для подгонки текста
assets/                         иконки слайдов, PNG-превью тем для API

tests/unit/                     юнит-тесты (unittest), LLM подменяется; test_api*.py — контракт второго бота
tests/gallery/                  стресс-галерея: все варианты × темы × заполнения, без ключей
tests/golden/                   реальные генерации корпуса G-01…G-06 и проверки колод (check_case.py)
tests/fixtures/                 входные файлы golden-кейсов
scripts/                        служебные: превью тем, иконки, таблицы вместимости, зонд SiliconFlow
spikes/                         эксперименты фазы 0 (python-pptx, LibreOffice), в продакшене не участвуют
docs/                           документация (раздел «С чего начать»)
```

---

## Словарь

| Термин | Что значит | Где определено |
|---|---|---|
| **колода** (deck) | Одна презентация. Id вида `dk_01M4…` (ULID), строка в таблице `decks` | `core/models/ids.py`, `db/models.py` |
| **`DeckRequest`** | Параметры колоды: тема, материал, язык, аудитория, число слайдов, тема оформления. Хранится в `decks.request` | `core/models/request.py` |
| **`DeckSpec`** | Колода целиком как данные: мета и слайды с содержанием. Единственное промежуточное представление: все этапы после OUTLINE дописывают его, рендерер рисует его как есть | `core/models/deck.py` |
| **режим** (`mode`) | `topic` — колода по теме, модель пишет из общих знаний; `material` — по присланному тексту или файлу | `DeckRequest.mode` |
| **`source_mode`** | При материале: `strict` — только материал пользователя, `extend` — можно дополнить | `DeckRequest.source_mode` |
| **`SourceDigest`** | Что извлечено из материала: фрагменты текста и `datasets` | `core/models/digest.py` |
| **dataset** | Таблица чисел из материала с адресами ячеек. Значения диаграмм код берёт отсюда, модель их не пишет | `core/models/digest.py` |
| **outline**, **план** | Ответ OUTLINE: жанр источника, тезисы и `PlannedSlide` на каждый слайд | `core/models/outline.py` |
| **жанр** (`genre`) | Тип источника: `topic`, `report`, `requirements`, `plan`, `article`, `other`. От жанра зависят правила плана | `core/models/outline.py` |
| **kind** | Смысловой тип слайда: `title`, `statement`, `bullets`, `comparison`, `process`, `metrics`, `chart_series`, `chart_share`, `conclusion`, `closing`. Содержание слайда хранится по схеме kind | `core/models/deck.py` |
| **role** | Роль слайда в сюжете: `problem`, `goal`, `results`, `risks`… Влияет на текст, не на вид | `core/models/outline.py` |
| **вариант** (variant) | Раскладка слайда данного kind, например `bullets.cards_grid`. Один kind — несколько вариантов; смена варианта не требует LLM | `layouts/<kind>/<variant>.yaml`, `core/models/layout.py` |
| **`LayoutSpec`** | Загруженный YAML варианта: элементы, рамки, стили, вместимость слотов (`capacity`), условия применимости (`applies_when`) | `core/models/layout.py` |
| **тема** (theme) | Цветовое оформление: палитра, шрифт, декор. Не путать с темой презентации (`input.topic`) | `themes/*.yaml`, `core/models/theme.py` |
| **seed** | Число, от которого детерминированно зависит выбор вариантов: один seed — та же раскладка | `DeckRequest.seed`, `DeckSpec.meta.seed` |
| **fit**, **невлезание** | Подгонка текста под рамки. Текст никогда не режется: кегль → вариант → сокращение моделью → два слайда | `core/fitting/` |
| **деградация** | Запасной путь этапа вместо падения колоды; список — в `DeckSpec.degradations` | `core/pipeline.py` |
| **`DeckError`**, код ошибки | Колода не собралась: `OUTLINE_FAILED`, `RENDER_FAILED`, `UPLOAD_EXPIRED`… | `core/pipeline.py`, `docs/design/04_CONTRACTS.md` 7.3 |
| **stage** | Текущий этап колоды для прогресса: `ingest`, `outline`, `content`, `render`, `convert` | `decks.stage`, `GET /v1/decks/{id}` |
| **клиент API** | Внешний сервис с ключом (второй бот): суточный лимит, лимит в минуту, webhook | `api/store.py`, таблица `api_clients` |
| **uploads** | Временное хранилище присланных файлов и текстов; удаляются после обработки | `generation/uploads.py` |
| **golden-корпус**, G-01…G-06 | Набор реальных входов с ожиданиями к колоде; прогоняется настоящей моделью в CI | `docs/TZ.md` 7.4, `tests/golden/` |
| **галерея** | Все варианты × все темы × 4 заполнения без LLM; по ней владелец утверждает дизайн | `tests/gallery/` |
| **старый / новый движок** | Старый — HTML → PDF (`src/generation`), остался только для питч-дека. Новый — PPTX + PDF (`src/core`) | `dialog.engine_for` |

---

## Запуск

Python 3.10, как в продакшене и CI (3.11 тоже работает). Для PDF и галереи нужен LibreOffice (`libreoffice-impress`) и шрифты `fonts-liberation`, `fonts-dejavu-core`.

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt   # тем же python, которым запускаете тесты
```

**Тесты** — ключи и сеть не нужны, LLM подменяется:

```bash
python -m unittest discover -s tests/unit -t tests/unit                      # все
python -m unittest discover -s tests/unit -t tests/unit -p "test_api*.py"    # контракт REST API
```

**Посмотреть слайды без ключей** — галерея собирает все варианты через тот же путь, что продакшен, и кладёт `gallery.pdf`, миниатюры и отчёт проверок:

```bash
python tests/gallery/build_gallery.py out/gallery --themes business_slate --fixtures typical
```

**Настоящая колода без Telegram** — нужен ключ LLM (`OPENAI_API_KEY`, остальное — по `.env.example`):

```bash
python tests/golden/run_golden.py --out out/golden --only G-03      # доклад по теме; PPTX, PDF и DeckSpec — в out/golden
```

**Бот, воркер и API локально** — нужен Redis. Бот и воркер работают и без Postgres (пустой `DATABASE_URL`), API — нет: ключи клиентов хранятся в БД. Переменные — в `.env` по образцу `.env.example`:

```bash
cd src
python main.py                       # бот (нужен TELEGRAM_BOT_TOKEN)
python -m arq worker.WorkerSettings  # воркер (нужны OPENAI_API_KEY и TELEGRAM_BOT_TOKEN)
uvicorn api.app:app --port 8000      # REST API; ключ клиента: python -m api.keys --help
```

---

## Правила, которые нельзя нарушать

- **REST API — только обратно совместимые изменения.** Не удалять и не переименовывать поля, не менять типы. Изменили схему — обновить снимок (`python tests/unit/test_api_contract.py --update`) и `docs/API.md`.
- **Миграции — только добавляющие:** новые таблицы, nullable-колонки, индексы.
- **Промпты и параметры моделей — файлами в `prompts/`, не в коде.** Любая правка промпта — новая строка в `prompts/v2/VERSION` и прогон golden.
- **Вид слайдов — в `layouts/` и `themes/`, не в рендерере.** Рендерер рисует `DeckSpec` как есть и ничего не решает.
- **Текст на слайде не обрезается и не заканчивается «…».** Невлезание решает `core/fitting/resolve.py`.
- **Секреты — только в переменных окружения.** Настройки читаются только через `src/config.py`.
- **В `main` не пушить.** Работа идёт в ветке и PR, мёржит владелец. Подробно — `CLAUDE.md`.
- **Архитектурное решение — запись в `docs/DECISIONS.md`**, итог работы — запись в `docs/PROGRESS.md`.
