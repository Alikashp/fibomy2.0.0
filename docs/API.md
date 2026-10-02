# Fibonacci AI — REST API v1

Руководство для разработчика внешнего клиента (второго бота). API создаёт презентацию по теме, тексту или файлу и отдаёт два файла: редактируемый **PPTX** и **PDF**.

- Базовый адрес — публичный домен сервиса `api` в Railway, например `https://fibonacci-api.up.railway.app`. Дальше — `$API`.
- Все пути начинаются с `/v1`. Схема OpenAPI — `$API/v1/openapi.json`, интерактивная документация — `$API/v1/docs`.
- Генерация занимает десятки секунд, поэтому модель **асинхронная**: создать колоду → опрашивать статус → скачать файлы. Держать HTTP-соединение открытым до готовности не нужно и нельзя.
- Формат — JSON в UTF-8. Даты — ISO 8601 в UTC (`2026-10-01T13:07:36.681300Z`).

Содержание: [ключ](#1-ключ) · [быстрый старт](#2-быстрый-старт-curl) · [запросы](#3-запросы) · [статус колоды](#4-статус-колоды) · [webhook](#5-webhook) · [ошибки](#6-ошибки) · [лимиты](#7-лимиты) · [таймауты и повторы](#8-таймауты-повторы-опрос) · [пример на Python](#9-пример-на-python) · [совместимость](#10-совместимость-и-что-пока-не-поддерживается)

---

## 1. Ключ

Ключ выдаёт владелец сервиса. Разработчик клиента получает два значения:

| Значение | Вид | Зачем |
|---|---|---|
| API-ключ | `fib_…` (47 знаков) | заголовок каждого запроса |
| Секрет webhook | `whsec_…` | проверка подписи webhook (раздел 5); не нужен, если webhook не используется |

Оба показываются **один раз** при создании: в базе хранится только хэш ключа, восстановить его нельзя — только выпустить новый. Храните ключ в переменных окружения клиента (например, `FIBONACCI_API_KEY`), не в коде и не в репозитории.

Каждый запрос, кроме `GET /v1/health`, — с заголовком:

```
Authorization: Bearer fib_…
```

Нет ключа, ключ неверный или отозван — `401 UNAUTHORIZED`.

### 1.1 Для владельца: ключи — командами в Telegram-боте

Владелец управляет ключами прямо в основном боте, в личном чате с ним. Команды работают только для Telegram ID из переменной `ADMIN_TELEGRAM_IDS` сервиса бота; остальным бот отвечает так же, как на любую незнакомую команду.

| Команда | Что делает |
|---|---|
| `/apikey create Второй бот 200` | новый клиент с лимитом 200 колод в сутки (без числа — 100). Бот присылает API-ключ и секрет webhook **один раз** и **удаляет это сообщение через 5 минут** — скопируйте сразу. Водяной знак в PDF по умолчанию есть |
| `/apikey list` | клиенты: лимит, использовано сегодня и сколько осталось, водяной знак, стоимость за сегодня и за 30 дней |
| `/apikey limit Второй бот 500` | новый суточный лимит |
| `/apikey watermark Второй бот off` | убрать водяной знак в PDF (`on` — вернуть) |
| `/apikey revoke Второй бот` | отключить клиента — после подтверждения кнопкой; все его запросы получают 401 |
| `/apikey rotate Второй бот` | новый ключ (сообщение тоже удаляется через 5 минут); старый перестаёт работать сразу |

Имя клиента пишется как есть, регистр не важен; если имя кончается числом — в кавычках: `/apikey create "Бот 2" 300`. Вместо имени подойдёт id `ac_…`. Имена активных клиентов не повторяются.

### 1.2 То же из командной строки (для разработчика)

Та же логика (`api.admin`) доступна командой `python -m api.keys` в папке `src/` с `DATABASE_URL` в окружении — например, в контейнере сервиса `api`: `railway ssh --service api`, затем `cd src`. Без `railway ssh` — локально из клона: `DATABASE_URL=<DATABASE_PUBLIC_URL сервиса Postgres> python -m api.keys …`.

| Команда | Что делает |
|---|---|
| `create --name "Второй бот" [--daily-limit 100] [--rate-limit 120] [--decks-per-min 10] [--no-watermark]` | новый клиент; ключ и секрет webhook печатаются один раз |
| `list` | клиенты, лимиты, расход за сутки и 30 дней (ключ — только начало) |
| `set "Второй бот" --daily-limit 500 [--rate-limit …] [--decks-per-min …] [--watermark/--no-watermark]` | изменить лимиты; клиент — по имени или id |
| `rotate "Второй бот"` | выпустить новый ключ, старый перестаёт работать сразу |
| `revoke "Второй бот"` | отключить клиента (все запросы — 401) |
| `usage [--days 30]` | колоды, ошибки и себестоимость по клиентам |

Запросов в минуту и колод в минуту (`--rate-limit`, `--decks-per-min`) в боте нет — только в командной строке.

---

## 2. Быстрый старт (curl)

```bash
export API=https://fibonacci-api.up.railway.app
export KEY=fib_…

# 1. Создать колоду по теме
curl -sS -X POST "$API/v1/decks" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1001" \
  -d '{"input": {"topic": "Как работает фотосинтез"}, "slides_count": 7, "theme_id": "azure_coral"}'
# 202 {"id": "dk_01J…", "status": "queued", …}

export ID=dk_01J…

# 2. Опрашивать статус каждые 3–5 секунд до done или failed
while :; do
  s=$(curl -sS "$API/v1/decks/$ID" -H "Authorization: Bearer $KEY")
  st=$(echo "$s" | jq -r .status); echo "$st $(echo "$s" | jq -c '[.stage, .progress]')"
  [ "$st" = done ] || [ "$st" = failed ] && break
  sleep 4
done

# 3. Скачать файлы (тем же ключом)
curl -sS -o deck.pptx "$API/v1/decks/$ID/files/pptx" -H "Authorization: Bearer $KEY"
curl -sS -o deck.pdf  "$API/v1/decks/$ID/files/pdf"  -H "Authorization: Bearer $KEY"
```

С файлом-материалом — `multipart/form-data`: поле `params` с теми же параметрами в JSON и поле `file`:

```bash
curl -sS -X POST "$API/v1/decks" -H "Authorization: Bearer $KEY" \
  -F 'params={"input": {"topic": "Итоги продаж за квартал"}, "slides_count": 9, "source_mode": "strict"}' \
  -F "file=@report_q3.docx"
```

С текстом-материалом — JSON, поле `input.text`:

```bash
curl -sS -X POST "$API/v1/decks" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input": {"topic": "Итоги пилота", "text": "Пилот шёл с января по июнь 2026 года…"}, "audience": "management"}'
```

---

## 3. Запросы

| Метод | Путь | Что делает |
|---|---|---|
| `POST` | `/v1/decks` | создать колоду → `202` + статус |
| `GET` | `/v1/decks/{id}` | статус колоды |
| `GET` | `/v1/decks/{id}/files/pptx` | PPTX |
| `GET` | `/v1/decks/{id}/files/pdf` | PDF |
| `GET` | `/v1/themes` | темы оформления |
| `GET` | `/v1/usage` | лимиты ключа и сколько израсходовано сегодня |
| `GET` | `/v1/health` | состояние сервиса (без ключа) |

### 3.1 `POST /v1/decks` — создать колоду

Тело — `application/json`, либо `multipart/form-data` с полями `params` (JSON ниже, строкой) и `file`.

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `input.topic` | строка, 3–200 знаков | — **обязательно** | Тема. Ставится заголовком титульного слайда **дословно**. Длинный текст — в `input.text`, а не сюда |
| `input.text` | строка до 200 000 знаков | `null` | Текст-материал. В работу идут первые 40 000 знаков (остальное — предупреждение `MATERIAL_TRUNCATED`). Не вместе с файлом |
| `file` (multipart) | `.pdf`, `.docx`, `.pptx`, `.txt` до 20 МБ | — | Файл-материал. Тип — по расширению имени файла. PDF — до 50 страниц, PPTX — до 100 слайдов; скан без текстового слоя не читается |
| `presentation_type` | `doklad` | `doklad` | Тип презентации. `pitch_deck` пока возвращает `400 UNSUPPORTED_TYPE` |
| `slides_count` | целое 4–20 | `9` | Слайдов всего, вместе с титулом и финальным |
| `source_mode` | `strict` \| `extend` | `strict` | Только с материалом. `strict` — только факты материала; если материала мало, слайдов будет меньше (предупреждение `SLIDES_SHORT`). `extend` — ровно `slides_count` слайдов, недостающее — общими знаниями, без новых чисел. Без материала игнорируется |
| `language` | `ru`, `en`, `uz`, `kk` | `ru` | Язык текста слайдов |
| `audience` | `general`, `students`, `colleagues`, `management`, `clients`, `investors` | `general` | Для кого доклад: влияет на тон и подачу |
| `theme_id` | id из `GET /v1/themes` | `graphite_light` | Тема оформления |
| `author.name`, `author.group` | строки до 150 знаков | `null` | ФИО докладчика и группа / организация — на титуле и финальном слайде |
| `webhook_url` | `https://…` (или `http://`) до 2000 знаков | `null` | Куда прислать статус по готовности (раздел 5) |
| `seed` | целое | случайный | Повторяемый выбор вариантов вёрстки; обычно не нужен |

Неизвестное поле — ошибка `400 BAD_REQUEST` (защита от опечаток). Водяной знак в PDF задаётся настройкой ключа, а не запросом.

**Заголовок `Idempotency-Key`** (необязательный, до 128 знаков, например id заказа во втором боте). Повтор запроса с тем же ключом не создаёт вторую колоду, а возвращает **`200`** и статус уже созданной — параметры повтора не сравниваются. Используйте его, чтобы безопасно повторять `POST` после таймаута или обрыва связи. Ключ уникален в пределах одного API-ключа.

**Ответ `202 Accepted`** — тело как у `GET /v1/decks/{id}` (раздел 4), `status = "queued"`. Заголовки: `Location: /v1/decks/dk_…`, `X-Daily-Remaining` — сколько колод ещё можно создать сегодня.

Ошибки параметров (`400`, `413`) и лимитов (`429`) приходят сразу и колоду не создают. Ошибки чтения материала (скан, битый файл) — уже в статусе колоды: файл разбирает воркер.

### 3.2 `GET /v1/decks/{id}` — статус

Ответ `200` — статус (раздел 4). Колода другого ключа и несуществующая колода — одинаково `404 NOT_FOUND`.

### 3.3 `GET /v1/decks/{id}/files/{pptx|pdf}` — файлы

Ответ `200` — байты файла, `Content-Type` — `application/vnd.openxmlformats-officedocument.presentationml.presentation` или `application/pdf`, `Content-Disposition: attachment` с именем по теме (`filename*` в UTF-8). Ссылки из `files` в статусе ведут сюда же — скачивать их нужно **с тем же заголовком `Authorization`**.

| Ответ | Когда |
|---|---|
| `409 DECK_NOT_READY` | колода ещё в работе |
| `409 DECK_FAILED` | колода не собралась — файлов нет |
| `404 FILE_NOT_AVAILABLE` | PDF не получился (у колоды предупреждение `PDF_FAILED`); PPTX есть |
| `410 FILE_EXPIRED` | срок хранения файлов (`files.expires_at`) истёк |

**Файлы хранятся недолго:** сейчас **1 час** после готовности (точное время — `files.expires_at`; после подключения S3 — 24 часа). Скачайте файлы сразу, как только `status = "done"`, и отправьте пользователю — в Telegram удобнее отправить байты один раз и дальше пользоваться `file_id`.

### 3.4 `GET /v1/themes` — темы

```json
{"themes": [
  {"id": "graphite_light", "name": {"ru": "Графит светлая", "en": "Graphite light"}, "mode": "light", "tags": ["business", "academic"]},
  {"id": "graphite_dark", "name": {"ru": "Графит тёмная", "en": "Graphite dark"}, "mode": "dark", "tags": ["business", "tech"]},
  {"id": "azure_coral", "name": {"ru": "Лазурь", "en": "Azure"}, "mode": "light", "tags": ["bright", "education", "business"]},
  {"id": "fresh_green", "name": {"ru": "Свежая зелёная", "en": "Fresh green"}, "mode": "light", "tags": ["bright", "education", "eco"]}
]}
```

Список может пополняться — не зашивайте его в код, берите из ответа (его можно кэшировать на сутки).

### 3.5 `GET /v1/usage` — лимиты ключа

```json
{"client_id": "ac_01J…", "name": "Второй бот", "watermark": false,
 "daily_limit": 200, "used_today": 17, "remaining_today": 183, "resets_at": "2026-10-02T00:00:00Z",
 "rate_limit_per_min": 120, "decks_per_min": 10}
```

### 3.6 `GET /v1/health` — состояние

Без ключа. `200 {"status": "ok", "version": "1.0.0", "engine": "core-…", "db": "ok", "redis": "ok"}`; если база или очередь недоступны — `503` и `"status": "degraded"`.

---

## 4. Статус колоды

```json
{
  "id": "dk_01J9Z6Q4X1Y2Z3A4B5C6D7E8F9",
  "status": "processing",
  "stage": "content",
  "progress": {"slides_done": 4, "slides_total": 7},
  "title": "Как работает фотосинтез",
  "slides": null,
  "created_at": "2026-10-01T13:07:36.681300Z",
  "started_at": "2026-10-01T13:07:37.020000Z",
  "finished_at": null,
  "files": null,
  "warnings": [],
  "error": null
}
```

Готовая:

```json
{
  "id": "dk_01J9Z6Q4X1Y2Z3A4B5C6D7E8F9", "status": "done", "stage": null, "progress": null,
  "title": "Итоги продаж за квартал", "slides": 7,
  "created_at": "…", "started_at": "…", "finished_at": "2026-10-01T13:08:21.410000Z",
  "files": {
    "pptx": "https://fibonacci-api.up.railway.app/v1/decks/dk_01J…/files/pptx",
    "pdf": "https://fibonacci-api.up.railway.app/v1/decks/dk_01J…/files/pdf",
    "expires_at": "2026-10-01T14:08:21.410000Z"
  },
  "warnings": [{"code": "SLIDES_SHORT", "message": "В материале хватило на 7 слайдов из 9. Добавьте текст, если нужно больше."}],
  "error": null
}
```

| Поле | Описание |
|---|---|
| `status` | `queued` — в очереди; `processing` — собирается; `done` — готова; `failed` — не собралась. `done_pdf_pending` (зарезервировано: PPTX готов, PDF догоняет) — обрабатывайте как `done`, `files.pdf` может быть `null` |
| `stage` | только при `processing`: `ingest` (чтение материала) → `outline` (план) → `content` (слайды) → `render` (PPTX) → `convert` (PDF). Список может пополниться |
| `progress` | только на этапе `content`: сколько слайдов готово |
| `slides` | число слайдов готовой колоды |
| `files` | при `done`: ссылки на скачивание (раздел 3.3) и срок хранения; `pdf: null` — PDF не получился |
| `warnings` | предупреждения готовой колоды — их стоит показать пользователю (`message` написан для него) |
| `error` | при `failed`: `{"code", "message"}` (раздел 6.2) |

Обычное время от создания до `done` — 30–90 секунд. Колода в работе дольше 15 минут отдаётся как `failed` с кодом `DEADLINE_EXCEEDED`.

**Предупреждения:**

| Код | Что значит |
|---|---|
| `SLIDES_SHORT` | `source_mode = strict`: материала хватило на меньшее число слайдов, чем запрошено |
| `MATERIAL_TRUNCATED` | материал длиннее 40 000 знаков — в работу пошло начало |
| `PDF_FAILED` | PDF не собрался; PPTX — полноценный файл |

---

## 5. Webhook

Если в запросе был `webhook_url`, по завершении колоды (`done` или `failed`) сервис отправит:

```
POST <webhook_url>
Content-Type: application/json
X-Fibonacci-Signature: sha256=<hex HMAC-SHA256 тела секретом whsec_…>
X-Fibonacci-Deck-Id: dk_…
X-Fibonacci-Attempt: 1
```

Тело — тот же статус, что отдаёт `GET /v1/decks/{id}`. Ответьте `2xx` в течение 10 секунд; иначе будет ещё 3 попытки — через 10 с, 1 мин и 5 мин. Редиректы не выполняются.

Проверка подписи (Python):

```python
import hashlib, hmac

def webhook_is_valid(raw_body: bytes, signature_header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")
```

Подпись считается от **сырого тела** запроса — до разбора JSON. Webhook — подсказка, а не гарантия доставки: держите опрос статуса как основной путь (или как запасной, если webhook не пришёл за 2–3 минуты).

---

## 6. Ошибки

Формат любой ошибки запроса:

```json
{"error": {"code": "DAILY_LIMIT_EXCEEDED", "message": "Исчерпан суточный лимит ключа: 200 колод. Счётчик обнулится в 00:00 UTC."}}
```

Ветвитесь по `code`; `message` — на русском, для разработчика и пользователя. Новые коды могут появиться — неизвестный код обрабатывайте как общую ошибку.

### 6.1 Коды HTTP-ответов

| HTTP | Код | Когда | Что делать |
|---|---|---|---|
| 400 | `BAD_REQUEST` | неверные параметры, неизвестное поле, не JSON, файл пустой, файл и текст вместе | исправить запрос (`message` называет поле) |
| 400 | `UNSUPPORTED_TYPE` | `presentation_type` — не `doklad` (питч-дек пока не поддерживается) | использовать `doklad` |
| 400 | `FILE_UNSUPPORTED` | расширение файла не `.pdf`, `.docx`, `.pptx`, `.txt` | другой файл или текст |
| 413 | `FILE_TOO_LARGE` | файл больше 20 МБ или JSON-тело больше 1 МБ | файл меньше; длинный текст — файлом `.txt` |
| 401 | `UNAUTHORIZED` | нет ключа, ключ неверный или отозван | проверить `Authorization` |
| 404 | `NOT_FOUND` | нет такой колоды (или она другого ключа), неверный адрес, формат файла не `pptx`/`pdf` | — |
| 404 | `FILE_NOT_AVAILABLE` | PDF у колоды не получился | скачать PPTX |
| 405 | `METHOD_NOT_ALLOWED` | метод не поддерживается для адреса | — |
| 409 | `DECK_NOT_READY` | файлы запрошены до `done` | дождаться `done` |
| 409 | `DECK_FAILED` | файлы запрошены у упавшей колоды | смотреть `error` в статусе |
| 410 | `FILE_EXPIRED` | срок хранения файлов истёк | создать колоду заново |
| 429 | `RATE_LIMITED` | больше запросов или новых колод в минуту, чем позволяет ключ | ждать `Retry-After` секунд |
| 429 | `DAILY_LIMIT_EXCEEDED` | исчерпан суточный лимит колод | ждать до `resets_at` (заголовок `Retry-After`) |
| 500 | `INTERNAL` | внутренняя ошибка | повторить позже (для `POST` — с тем же `Idempotency-Key`) |
| 503 | `SERVICE_UNAVAILABLE` | недоступны база, очередь или хранилище | повторить через 5–30 с |

### 6.2 Ошибки колоды (в статусе, `status = "failed"`)

| Код | Когда | Что сказать пользователю |
|---|---|---|
| `SCAN_WITHOUT_TEXT` | PDF без текстового слоя (скан) | прислать файл с текстом или текст сообщением |
| `BAD_FILE` | файл повреждён или не читается | пересохранить файл или прислать текст |
| `PARSE_TIMEOUT` | файл читается дольше 15 с | файл поменьше или текст |
| `FILE_UNSUPPORTED` | содержимое не соответствует формату | другой файл |
| `UPLOAD_EXPIRED` | колода ждала в очереди дольше часа, материал удалён | создать заново |
| `OUTLINE_FAILED` | модель не составила план | повторить |
| `RENDER_FAILED` | не собрался файл | сообщить id колоды владельцу сервиса |
| `STORAGE_FAILED` | колода собрана, но файлы не сохранились | повторить |
| `DEADLINE_EXCEEDED` | колода не собралась за отведённое время | повторить |
| `INTERNAL` | прочее | повторить; если повторяется — сообщить id колоды |

Упавшая колода **не списывает** суточный лимит.

---

## 7. Лимиты

Лимиты задаются на ключ (раздел 1.1, значения — `GET /v1/usage`):

| Лимит | По умолчанию | При превышении |
|---|---|---|
| колод в сутки (`daily_limit`) | 100 | `429 DAILY_LIMIT_EXCEEDED` |
| новых колод в минуту (`decks_per_min`) | 10 | `429 RATE_LIMITED` |
| любых запросов в минуту (`rate_limit_per_min`) | 120 | `429 RATE_LIMITED` |

- **Сутки — календарные, UTC:** счётчик обнуляется в 00:00 UTC (03:00 МСК). В лимит идут колоды в очереди, в работе и готовые; упавшие — нет. Повтор с тем же `Idempotency-Key` лимит не тратит.
- **Минута — окно календарной минуты.** Каждый ответ с ключом несёт заголовки `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (секунд до нового окна); ответ `429` — ещё `Retry-After`.
- Опрос одной колоды раз в 4 секунды — 15 запросов в минуту, то есть лимита 120 хватает на ~7 колод в работе одновременно. Нужно больше — попросите владельца поднять лимит.

---

## 8. Таймауты, повторы, опрос

| Что | Рекомендация |
|---|---|
| Подключение | 10 с |
| `POST /v1/decks` с файлом | таймаут чтения 60 с |
| `GET` статуса | 10 с |
| Скачивание файла | 60 с |
| Интервал опроса статуса | **3–5 с** (не чаще раза в 2 с) |
| Сколько ждать колоду | до **5 минут** с момента создания, затем считать неудачей и сообщить пользователю |
| Ошибка сети, `500`, `503` | повтор с паузой 2, 4, 8 с (до 3 раз); `POST` — **с тем же `Idempotency-Key`** |
| `429` | ждать `Retry-After` секунд и повторить |
| `4xx` кроме `429` | не повторять: запрос неверный |

Не держите соединение открытым в ожидании готовности и не опрашивайте чаще раза в 2 секунды — это расходует лимит запросов, а колода быстрее не станет.

---

## 9. Пример на Python

Асинхронный клиент на `httpx` (подходит для бота на aiogram): создать колоду → опрашивать статус каждые 4 с → скачать файлы.

```python
import asyncio
import json
import os
import uuid

import httpx

API = os.environ["FIBONACCI_API_URL"]          # https://fibonacci-api.up.railway.app
KEY = os.environ["FIBONACCI_API_KEY"]          # fib_…
POLL_SECONDS = 4
WAIT_SECONDS = 300


class DeckError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


async def _request(http: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """Повторы при сбоях сети, 5xx и 429."""
    for attempt in range(4):
        try:
            r = await http.request(method, url, **kw)
        except httpx.TransportError:
            if attempt == 3:
                raise
            await asyncio.sleep(2 ** (attempt + 1))
            continue
        if r.status_code == 429 and attempt < 3:
            await asyncio.sleep(int(r.headers.get("Retry-After", "5")))
            continue
        if r.status_code >= 500 and attempt < 3:
            await asyncio.sleep(2 ** (attempt + 1))
            continue
        return r
    return r


def _raise_for_error(r: httpx.Response) -> None:
    if r.status_code >= 400:
        err = r.json().get("error", {})
        raise DeckError(err.get("code", "INTERNAL"), err.get("message", r.text))


async def make_deck(topic: str, *, text: str | None = None, file_path: str | None = None,
                    slides: int = 9, theme: str = "graphite_light", audience: str = "general",
                    order_id: str | None = None) -> dict:
    """→ {"deck": статус, "pptx": bytes, "pdf": bytes | None}."""
    params = {"input": {"topic": topic, "text": text}, "slides_count": slides,
              "theme_id": theme, "audience": audience}
    headers = {"Authorization": f"Bearer {KEY}"}
    idem = {"Idempotency-Key": order_id or uuid.uuid4().hex}   # один на колоду, для всех повторов POST
    timeout = httpx.Timeout(60, connect=10)
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=timeout) as http:
        if file_path:
            with open(file_path, "rb") as f:
                content = f.read()
            r = await _request(http, "POST", "/v1/decks", headers=idem,
                               data={"params": json.dumps(params, ensure_ascii=False)},
                               files={"file": (os.path.basename(file_path), content)})
        else:
            r = await _request(http, "POST", "/v1/decks", headers=idem, json=params)
        _raise_for_error(r)
        deck = r.json()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + WAIT_SECONDS
        while deck["status"] in ("queued", "processing"):
            if loop.time() > deadline:
                raise DeckError("CLIENT_TIMEOUT", "Колода не готова за 5 минут")
            await asyncio.sleep(POLL_SECONDS)
            r = await _request(http, "GET", f"/v1/decks/{deck['id']}", timeout=10)
            _raise_for_error(r)
            deck = r.json()
            # deck["stage"], deck["progress"] — можно показывать пользователю

        if deck["status"] == "failed":
            raise DeckError(deck["error"]["code"], deck["error"]["message"])

        # done (и зарезервированный done_pdf_pending): скачать сразу — файлы живут недолго
        pptx = await _request(http, "GET", deck["files"]["pptx"])
        _raise_for_error(pptx)
        pdf = None
        if deck["files"]["pdf"]:
            r = await _request(http, "GET", deck["files"]["pdf"])
            pdf = r.content if r.status_code == 200 else None
        return {"deck": deck, "pptx": pptx.content, "pdf": pdf}


if __name__ == "__main__":
    result = asyncio.run(make_deck("Как работает фотосинтез", slides=7))
    open("deck.pptx", "wb").write(result["pptx"])
    if result["pdf"]:
        open("deck.pdf", "wb").write(result["pdf"])
    for w in result["deck"]["warnings"]:
        print(w["message"])
```

Отправка в Telegram (aiogram 3): `BufferedInputFile(result["pptx"], filename="deck.pptx")`; предупреждения `warnings[].message` — в подпись.

---

## 10. Совместимость и что пока не поддерживается

- **Версия v1 меняется только обратно совместимо:** новые необязательные поля запроса, новые поля ответа, новые коды ошибок и предупреждений, новые значения `stage`, новые темы. Поля не удаляются и не переименовываются. Поэтому клиент должен **игнорировать незнакомые поля** ответа и обрабатывать незнакомые коды как общую ошибку. Несовместимые изменения — только в `/v2`.
- Пока только **доклад** (`doklad`). Питч-дек появится, когда переедет на новый движок, — тот же запрос с `presentation_type = "pitch_deck"`.
- Языки — `ru`, `en`, `uz`, `kk`. Картинок на слайдах пока нет; в «по теме» нет диаграмм (числовые диаграммы строятся только из таблиц материала).
- Правки готовой колоды через API не предусмотрены — новая колода создаётся новым запросом.
- Файлы колоды хранятся 1 час (до подключения S3, затем 24 часа) — ориентируйтесь на `files.expires_at`.
