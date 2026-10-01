"""REST API для внешних клиентов (второй бот): docs/API.md, 04_CONTRACTS.md, 7; D-055.

Отдельный сервис Railway «api» из того же образа: uvicorn api.app:app. Колоду
строит тот же воркер (generate_deck_job), что и для бота: API создаёт строку decks
и ставит задачу в очередь ARQ.

Модули без FastAPI (errors, schemas, status, store, limits, auth, webhook) импортирует
и воркер; FastAPI — только в app.py.
"""

API_VERSION = "1.0.0"
