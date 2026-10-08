"""Уведомление владельцу: клиент API израсходовал 80% и 100% суточного лимита (сессия 4, D-068).

По одному сообщению на порог в сутки UTC: ключ Redis api:limit_alert:<клиент>:<дата>:<порог>
ставится SET NX на двое суток — второй запрос того же порога сообщения не шлёт. Сообщение
отправляет воркер (задача notify_admins_job: у сервиса api нет Telegram-токена) всем
ADMIN_TELEGRAM_IDS в личку; в тексте — команда бота для повышения лимита. Redis или очередь
недоступны — уведомление пропускается, запрос клиента от этого не страдает.
"""

import html
import logging
import math
from datetime import datetime, timezone

from core.storage.redis import get_redis

logger = logging.getLogger(__name__)

THRESHOLDS = (80, 100)
TTL_SECONDS = 2 * 86400


def level(used: int, limit: int) -> int | None:
    """Порог, которого достиг расход: 100, 80 или None."""
    if limit <= 0:
        return None
    if used >= limit:
        return 100
    if used >= math.ceil(limit * 0.8):
        return 80
    return None


def text(name: str, used: int, limit: int, pct: int) -> str:
    name_html = html.escape(name)
    raise_to = max(limit * 2, limit + 10)
    head = (f"⚠️ Клиент API <b>{name_html}</b> израсходовал 80% суточного лимита: {used} из {limit} колод."
            if pct == 80 else
            f"⛔ Клиент API <b>{name_html}</b> исчерпал суточный лимит: {used} из {limit} колод. "
            f"Новые колоды получают 429 DAILY_LIMIT_EXCEEDED до 00:00 UTC.")
    return (f"{head}\nСчётчик обнулится в 00:00 UTC.\n\n"
            f"Повысить лимит:\n<code>/apikey limit {name_html} {raise_to}</code>")


async def notify(arq, client, used: int, now: datetime | None = None) -> int | None:
    """После создания колоды (used — колод клиента за сутки вместе с ней) или отказа по лимиту.
    → порог, о котором поставлено уведомление, или None."""
    from config import settings
    pct = level(used, client.daily_limit)
    if pct is None or not settings.admin_telegram_ids.strip() or arq is None:
        return None
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        redis = get_redis()
        first = await redis.set(f"api:limit_alert:{client.id}:{day}:{pct}", "1", nx=True, ex=TTL_SECONDS)
        if pct == 100:   # лимит исчерпан сразу — о 80% уже не пишем
            await redis.set(f"api:limit_alert:{client.id}:{day}:80", "1", nx=True, ex=TTL_SECONDS)
        if not first:
            return None
        await arq.enqueue_job("notify_admins_job", text(client.name, used, client.daily_limit, pct))
    except Exception as e:  # noqa: BLE001 — уведомление не должно ронять запрос клиента
        logger.warning("Limit alert not sent", extra={"api_client_id": client.id, "error": str(e)})
        return None
    logger.info("Limit alert enqueued", extra={"api_client_id": client.id, "threshold": pct, "used": used})
    return pct
