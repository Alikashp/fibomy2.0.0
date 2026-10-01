"""Строка decks: создание ботом, статус и результат — воркером (04_CONTRACTS.md, 8; D-034).

Без DATABASE_URL (тесты, golden, локальный запуск) функции ничего не делают:
get_session() отдаёт None.
"""

import logging
from datetime import datetime, timezone

from db.models import Deck
from db.session import get_session

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_deck(deck_id: str, request: dict, user_id: int | None,
                      parent_deck_id: str | None = None) -> bool:
    """INSERT decks (queued). False — БД не настроена."""
    async with get_session() as session:
        if session is None:
            return False
        session.add(Deck(id=deck_id, user_id=user_id, request=request, status="queued",
                         parent_deck_id=parent_deck_id))
        return True


async def load_request(deck_id: str) -> dict | None:
    async with get_session() as session:
        if session is None:
            return None
        deck = await session.get(Deck, deck_id)
        return dict(deck.request) if deck else None


async def mark_processing(deck_id: str) -> None:
    async with get_session() as session:
        if session is None:
            return
        deck = await session.get(Deck, deck_id)
        if deck:
            deck.status, deck.started_at = "processing", _now()


async def set_stage(deck_id: str, stage: str) -> None:
    async with get_session() as session:
        if session is None:
            return
        deck = await session.get(Deck, deck_id)
        if deck:
            deck.stage = stage


async def finish(deck_id: str, *, status: str, spec: dict | None, durations_ms: dict, usage: dict,
                 cost_rub: float | None, degradations: list, files: dict | None = None) -> None:
    async with get_session() as session:
        if session is None:
            return
        deck = await session.get(Deck, deck_id)
        if deck is None:
            logger.warning("Deck row not found on finish", extra={"deck_id": deck_id})
            return
        deck.status, deck.stage, deck.finished_at = status, None, _now()
        deck.spec, deck.durations_ms, deck.usage = spec, durations_ms, usage
        deck.cost_rub, deck.degradations, deck.files = cost_rub, degradations, files


async def fail(deck_id: str, code: str, *, durations_ms: dict | None = None, usage: dict | None = None,
               cost_rub: float | None = None, degradations: list | None = None) -> None:
    async with get_session() as session:
        if session is None:
            return
        deck = await session.get(Deck, deck_id)
        if deck is None:
            return
        deck.status, deck.error_code, deck.finished_at = "failed", code, _now()
        deck.durations_ms, deck.usage, deck.cost_rub, deck.degradations = durations_ms, usage, cost_rub, degradations


async def mark_counted(deck_id: str) -> None:
    """Генерация списана с лимита — после успешной отправки файлов (02_CJM.md, 1.2, шаг 8)."""
    async with get_session() as session:
        if session is None:
            return
        deck = await session.get(Deck, deck_id)
        if deck:
            deck.counted = True
