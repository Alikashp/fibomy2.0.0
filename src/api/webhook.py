"""Webhook по готовности колоды API (04_CONTRACTS.md, 7.3; docs/API.md).

POST на webhook_url из запроса: тело — DeckStatus (как GET /v1/decks/{id}),
заголовок X-Fibonacci-Signature: sha256=<HMAC-SHA256 тела секретом клиента>.
Ответ 2xx — доставлено; иначе 3 повтора через 10 с, 1 мин, 5 мин (отдельные задачи
ARQ, воркер не ждёт). Редиректы не выполняются. Webhook — подсказка, а не гарантия:
клиент всё равно опрашивает статус.
"""

import logging
from urllib.parse import urlparse

import httpx

from api import API_VERSION
from api.auth import sign
from api.schemas import DeckStatus
from api.status import build_status
from api import store
from config import settings
from core.storage import decks as deck_store

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
RETRY_DELAYS = (10, 60, 300)


def url_ok(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


async def send(deck_id: str, attempt: int = 1, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """True — доставлено или доставлять нечего (нет URL, нет клиента)."""
    deck = await deck_store.load(deck_id)
    if deck is None or not deck.api_client_id:
        return True
    url = (deck.request or {}).get("webhook_url")
    if not url or not url_ok(url):
        return True
    client = await store.get_client(deck.api_client_id)
    if client is None:
        return True
    body = DeckStatus(**build_status(deck, base_url=settings.api_public_url)).model_dump_json().encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"FibonacciAI-Webhook/{API_VERSION}",
        "X-Fibonacci-Signature": sign(client.webhook_secret, body),
        "X-Fibonacci-Deck-Id": deck_id,
        "X-Fibonacci-Attempt": str(attempt),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=transport) as http:
            response = await http.post(url, content=body, headers=headers)
    except httpx.HTTPError as e:
        logger.warning("Webhook failed", extra={"deck_id": deck_id, "attempt": attempt, "error": str(e)})
        return False
    if 200 <= response.status_code < 300:
        logger.info("Webhook delivered", extra={"deck_id": deck_id, "attempt": attempt})
        return True
    logger.warning("Webhook rejected", extra={"deck_id": deck_id, "attempt": attempt,
                                              "http_status": response.status_code})
    return False


async def deliver_webhook_job(ctx: dict, deck_id: str, attempt: int = 1) -> dict:
    """Задача воркера: попытка доставки; при неудаче — следующая попытка с задержкой."""
    ok = await send(deck_id, attempt)
    if not ok and attempt <= len(RETRY_DELAYS) and ctx.get("redis") is not None:
        await ctx["redis"].enqueue_job("deliver_webhook_job", deck_id, attempt=attempt + 1,
                                       _defer_by=RETRY_DELAYS[attempt - 1])
    return {"deck_id": deck_id, "attempt": attempt, "delivered": ok}
