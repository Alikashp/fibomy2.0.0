"""Частота запросов ключа — фиксированное окно в минуту в Redis (D-057).

Два счётчика на ключ: все запросы (rate_limit_per_min) и создание колод
(decks_per_min). Суточный лимит колод считается по decks в БД (api.store).
Redis недоступен — запрос пропускается (без Redis не работает и очередь, это
покажет POST ответом 503), а не отклоняется.
"""

import logging
import time
from dataclasses import dataclass

from core.storage.redis import get_redis

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


@dataclass
class Window:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int        # секунд до начала следующего окна

    def headers(self) -> dict:
        return {"X-RateLimit-Limit": str(self.limit), "X-RateLimit-Remaining": str(self.remaining),
                "X-RateLimit-Reset": str(self.reset_in)}


async def hit(scope: str, client_id: str, limit: int, now: float | None = None) -> Window:
    now = time.time() if now is None else now
    window = int(now // WINDOW_SECONDS)
    reset_in = WINDOW_SECONDS - int(now % WINDOW_SECONDS)
    key = f"api:rl:{scope}:{client_id}:{window}"
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, WINDOW_SECONDS + 5)
    except Exception as e:
        logger.warning("Rate limiter unavailable — request allowed", extra={"error": str(e)})
        return Window(True, limit, limit, reset_in)
    return Window(count <= limit, limit, max(0, limit - count), reset_in)
