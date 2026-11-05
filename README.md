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
| Как задуман новый движок | `docs/design/` — проект, написанный до кода; что из него сделано и где он расходится с кодом — `docs/design/README.md`. Архитектура — `docs/design/03_ARCHITECTURE.md`, макеты — `docs/design/05_LAYOUTS.md`, промпты — `docs/design/06_PROMPTS.md`, тесты — `docs/design/07_TESTING.md` |
| Как устроена конкретная фича и где её подводные камни | `docs/features/` — по файлу на фичу: диалог бота, материал, план, текст, подгонка, рендер, картинки, REST API, тарифы, golden и галерея |
| Почему сделано так, а не иначе | `docs/DECISIONS.md`: индекс в начале, в коде ссылки вида `D-071` ведут на запись там |
| Что проверить перед PR и на ревью | `docs/REVIEW.md` — блокеры B1–B14 |
| Что сделано и что дальше | `docs/PROGRESS.md`, новые записи сверху |
| Контракт REST API | `docs/API.md` |
| Правила работы с репозиторием | `CLAUDE.md`, разделы «Правила» (написан для ИИ-ассистента, но правила общие) |

`docs/CURRENT_STATE.md` — снимок кода на 26.09.2026, до нового движка. Он исторический, текущее устройство описывает этот файл.

**Как ведём документацию** (D-080):
- **Код — источник истины.** Документ, разошедшийся с кодом, правится в той же задаче, где это найдено. Требования к тому, каким код должен быть, задаёт `docs/TZ.md`.
- **Документы хранят решения и объяснения, а не перечисления.** Число вариантов, список тем, переменные окружения, роуты — смотрим в источнике (`layouts/`, `themes/`, `.env.example`, `tests/unit/api_contract_v1.json`) или генерируем скриптом, а не переписываем руками.
- **Не подтверждено кодом — так и пишем** («не сделано», «не сверялось»), а не держим в голове.
- **Проверка в CI:** `python scripts/check_docs.py` — пути в README, CLAUDE.md, `docs/API.md`, `docs/REVIEW.md` и `docs/features/` существуют; каждый роут API описан в `docs/API.md`; каждый документ есть в своей карте; индекс `docs/DECISIONS.md` актуален. Запускается юнит-тестом `tests/unit/test_docs.py`.

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

**Служебные команды:**

| Команда | Что делает |
|---|---|
| `python tests/golden/check_case.py <deck.json> <G-01…G-06>` | Проверить колоду по ожиданиям golden-кейса (ТЗ 7.4) |
| `python tests/golden/prompt_snapshot.py <папка>` | Снимок собранных промптов — сравнить до и после правки `prompts/` |
| `python scripts/layout_tables.py --write` | Пересобрать таблицы вместимости слотов в `docs/design/05_LAYOUTS.md` из YAML |
| `python scripts/build_theme_previews.py <папка>` | PNG-превью тем для `GET /v1/themes` (`assets/theme_previews/`) |
| `python scripts/check_docs.py` | Проверка документации (то же делает юнит-тест) |
| `python scripts/decisions_index.py` | Пересобрать индекс в начале `docs/DECISIONS.md` после новой записи |
| `python -m api.keys create\|list\|set\|rotate\|revoke\|usage` (из `src/`) | Клиенты и ключи REST API. Владелец делает то же командой бота `/apikey` (`bot/admin.py`, доступ — `ADMIN_TELEGRAM_IDS`) |

---

## Внешние сервисы

| Сервис | Для чего | Где в коде и настройки |
|---|---|---|
| **OpenAI API** (напрямую, D-078) | OUTLINE, CONTENT, сокращение и исправление текста | `core/llm/`; модель — `OPENAI_MODEL`, уровень рассуждений — `OPENAI_REASONING_EFFORT`, адрес — `OPENAI_BASE_URL`. Параметры запроса и цены по провайдеру — `prompts/models.yaml`: доллары переводятся в рубли по `USD_RUB_RATE`, `decks.cost_rub` и лимиты API — в рублях. На 429 вызов ждёт по `retry-after` и повторяется; одновременных вызовов на процесс — не больше `LLM_MAX_CONCURRENCY`. Откат на ProxyAPI — сменой `OPENAI_BASE_URL` и ключа, цены переключатся сами |
| **SiliconFlow**, Z-Image-Turbo (D-067) | ИИ-картинки: титул и 2 слайда | `core/images/`, параметры — `prompts/v2/images/`, ключ — `SILICONFLOW_API_KEY` (без ключа колоды без картинок). Надпись провайдера «AI 生成» срезается по правилу `marking` в `prompts/v2/images/config.yaml` (D-079) |
| **LibreOffice** | PPTX → PDF | `core/render/pdf/convert.py`; в образе и в CI ставится пакетом |
| **PostgreSQL** | пользователи, колоды (`decks`), клиенты API | `db/models.py`, миграции — `db/migrations/` (только добавляющие) |
| **Redis** | очередь ARQ, состояние диалога бота, временные файлы, прогресс; файлы колод API без S3 — на 1 ч | `REDIS_URL` |
| **S3-совместимое хранилище** (необязательно) | файлы колод API на 24 ч (D-058) | `core/storage/files.py`, переменные `S3_*` |

Все переменные окружения — в `.env.example`, читаются только через `src/config.py`.

## Деплой и CI

**Railway:** бот, воркер и API — три сервиса из одного Docker-образа (`Dockerfile`). Процесс выбирает переменная сервиса `SERVICE_ROLE` — `bot`, `worker` или `api` (`src/start.py`, D-062). Файлы `railway*.toml` (Config as Code) Railway отключает, на них не полагаться. **Порядок выкатки: воркер раньше бота и API.**

**GitHub Actions:**

| Workflow | Когда | Что делает |
|---|---|---|
| `tests` | каждый PR | Юнит-тесты, отдельный шаг «API-тесты (контракт второго бота)», снимок промптов относительно базы, `check_case.py` на колодах старого движка; job `gallery` — стресс-галерея и артефакт `gallery-pdf`, по которому владелец утверждает дизайн |
| `golden` | PR, меняющий промпты, ядро, API, воркер, макеты или темы (пути — `on.pull_request.paths` в `.github/workflows/golden.yml`); вручную | Реальные генерации G-01…G-06 и питч-дека, одна колода через REST API end-to-end; комментарий с таблицей в PR, артефакт `golden-decks`. Нужен секрет `OPENAI_API_KEY` (картинки — `SILICONFLOW_API_KEY`) |
| `siliconflow-probe` | вручную или при правке зонда | Картинки SiliconFlow: замер надписи провайдера или «до / после» обрезки (`scripts/probe_siliconflow_watermark.py`) |

---

## Правила, которые нельзя нарушать

- **REST API — только обратно совместимые изменения.** Не удалять и не переименовывать поля, не менять типы. Изменили схему — обновить снимок (`python tests/unit/test_api_contract.py --update`) и `docs/API.md`.
- **Миграции — только добавляющие:** новые таблицы, nullable-колонки, индексы.
- **Промпты и параметры моделей — файлами в `prompts/`, не в коде.** Любая правка промпта — новая строка в `prompts/v2/VERSION` и прогон golden.
- **Вид слайдов — в `layouts/` и `themes/`, не в рендерере.** Рендерер рисует `DeckSpec` как есть и ничего не решает.
- **Текст на слайде не обрезается и не заканчивается «…».** Невлезание решает `core/fitting/resolve.py`; в схемах ответа LLM нет `maxLength`/`maxItems` — длину проверяет код (D-071).
- **Секреты — только в переменных окружения.** Настройки читаются только через `src/config.py`.
- **В `main` не пушить.** Работа идёт в ветке и PR, мёржит владелец. Подробно — `CLAUDE.md`.
- **Архитектурное решение — запись в `docs/DECISIONS.md`**, итог работы — запись в `docs/PROGRESS.md`.
