# REST API

**Назначение:** генерация колод для внешних клиентов — сейчас второй бот владельца с живыми пользователями. Контракт для клиента — `docs/API.md`; здесь — как API устроен внутри.

## Как работает

1. **Ключ.** Клиент передаёт `Authorization: Bearer fib_…`. В БД хранится только SHA-256 ключа (`api_clients.key_hash`), сам ключ показывается один раз при создании. Отключённый клиент — 401.
2. **Лимиты частоты** (`api.limits`): фиксированное окно в минуту в Redis — все запросы (`rate_limit_per_min`) и создание колод (`decks_per_min`). Заголовки `X-RateLimit-*` — на всех ответах.
3. **`POST /v1/decks`:**
   - JSON или multipart с файлом;
   - повтор с тем же `Idempotency-Key` возвращает ту же колоду (200);
   - материал — во временное хранилище (`material.md`);
   - строка `decks` создаётся в одной транзакции с проверкой суточного лимита (`api.store.create_deck`);
   - задача `generate_deck_job` в очередь, ответ 202.
4. **Сборка** — тот же `generate_deck_job` и то же ядро, что у бота (`worker.py`). Отличия для клиента API:
   - файлы кладутся в хранилище (`core.storage.files.put_deck_files`);
   - прогресс CONTENT пишется в Redis (`core.storage.progress`), чтобы `GET` показывал `content N/M`;
   - по готовности ставится задача webhook.
5. **`GET /v1/decks/{id}`** — статус из строки `decks` (`api.status.build_status`), только своей колоды. **`GET /v1/decks/{id}/files/{pptx|pdf}`** — файл из хранилища, пока не истёк срок.
6. **Webhook** (`api.webhook.deliver_webhook_job`): POST тела статуса на `webhook_url` с подписью `X-Fibonacci-Signature: sha256=<HMAC тела секретом клиента>`. Не 2xx — повторы через 10 с, 1 мин и 5 мин отдельными задачами ARQ. Редиректы не выполняются.
7. **Темы** — `GET /v1/themes` с описанием, цветами и `preview_url`. `GET /v1/themes/{id}/preview.png` отдаётся без ключа (D-077).
8. **Управление клиентами** — одна логика `api.admin` для CLI `python -m api.keys` и команды бота `/apikey` (только `ADMIN_TELEGRAM_IDS`, только в личке). На 80% и 100% суточного лимита клиента владелец получает сообщение в Telegram (`api.limit_alerts`, D-068); отправляет его воркер — у сервиса api нет Telegram-токена.

## Где в коде

- `src/api/app.py` — эндпоинты, разбор запроса, ошибки; `src/api/schemas.py` — модели запросов и ответов.
- `src/api/store.py` — клиенты и колоды клиента в БД, суточный лимит; `src/api/limits.py` — окно в минуту.
- `src/api/auth.py` — формат и хэш ключа; `src/api/keys.py` — CLI; `src/api/admin.py` — общие операции; `src/bot/admin.py` — `/apikey`.
- `src/api/webhook.py`, `src/api/limit_alerts.py`, `src/api/status.py`, `src/api/previews.py`, `src/api/errors.py`.
- `src/core/storage/files.py` — файлы колод.
- Снимок контракта — `tests/unit/api_contract_v1.json`; тесты — `tests/unit/test_api*.py`; end-to-end с настоящими процессами — `tests/golden/run_api_e2e.py`.

## Нюансы

- **Живые пользователи: только обратно совместимые изменения** (D-059, правила — `README.md`, «Правила»). Изменили схему — `python tests/unit/test_api_contract.py --update` и `docs/API.md` в том же PR; `scripts/check_docs.py` проверит, что каждый роут описан.
- **Суточный лимит считается по строкам `decks`** за сутки UTC без упавших (`status != failed`): упавшая колода лимит не тратит. Значения по умолчанию у нового клиента — в `src/db/models.py` (`ApiClient`).
- **Файлы колод:** с S3 — 24 ч, без S3 — Redis на 1 ч (D-058). Сейчас на проде S3 нет, поэтому клиент должен скачать файлы в течение часа.
- **Без БД API не работает** (503): ключи клиентов хранятся в Postgres. Без Redis лимиты частоты пропускаются, но и очередь недоступна — `POST` отвечает 503.
- **Webhook — подсказка, а не гарантия:** клиент всё равно опрашивает статус.
- **Колода API помечается списанной (`counted`), как только файлы в хранилище**, без отправки в Telegram.
- **Ключ нигде не логируется;** сообщение бота с новым ключом удаляется через 5 минут задачей `delete_message_job`.
