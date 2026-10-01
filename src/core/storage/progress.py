"""Прогресс колоды «слайдов готово из N» — в Redis, а не в decks: правится на каждом
слайде, а нужен только статусу API (04_CONTRACTS.md, 7.2). Сбой Redis не валит колоду."""

import logging

from core.storage.redis import get_redis

logger = logging.getLogger(__name__)

PROGRESS_TTL_SECONDS = 2 * 60 * 60


def _key(deck_id: str) -> str:
    return f"deck:progress:{deck_id}"


async def set_progress(deck_id: str, done: int, total: int) -> None:
    try:
        await get_redis().set(_key(deck_id), f"{done}/{total}", ex=PROGRESS_TTL_SECONDS)
    except Exception as e:
        logger.debug("Progress not saved", extra={"error": str(e)})


async def get_progress(deck_id: str) -> tuple[int, int] | None:
    try:
        raw = await get_redis().get(_key(deck_id))
    except Exception as e:
        logger.warning("Progress not read", extra={"error": str(e)})
        return None
    if not raw:
        return None
    done, _, total = (raw.decode() if isinstance(raw, bytes) else raw).partition("/")
    try:
        return int(done), int(total)
    except ValueError:
        return None
