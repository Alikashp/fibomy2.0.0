"""Управление клиентами API — общий код для CLI (python -m api.keys) и админ-команд
бота (/apikey, bot/admin.py; D-061). Здесь — проверки и операции, там — только ввод и
вывод. Ключ возвращается вызывающему один раз и нигде не логируется.

Клиента можно указать по id (ac_…) или по имени без учёта регистра: имена активных
клиентов уникальны (проверяется при создании и переименовании).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from api import store
from db.models import ApiClient

logger = logging.getLogger(__name__)

NAME_MAX = 128
DAILY_LIMIT_MAX = 100_000


class AdminError(Exception):
    """Понятная ошибка для владельца: показать текст как есть."""


@dataclass
class ClientUsage:
    client: ApiClient
    used_today: int          # колоды суток, которые идут в лимит (без упавших)
    cost_today: float
    decks_30d: int
    failed_30d: int
    cost_30d: float

    @property
    def remaining_today(self) -> int:
        return max(0, self.client.daily_limit - self.used_today)


def _name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise AdminError("Нужно имя клиента, например «Второй бот».")
    if len(name) > NAME_MAX:
        raise AdminError(f"Имя длиннее {NAME_MAX} знаков.")
    if name.lower().startswith("ac_"):
        raise AdminError("Имя не может начинаться с «ac_» — так начинаются id клиентов.")
    return name


def _limit(value: int) -> int:
    if not 1 <= value <= DAILY_LIMIT_MAX:
        raise AdminError(f"Лимит — целое число от 1 до {DAILY_LIMIT_MAX}.")
    return value


async def resolve(ref: str, *, active_only: bool = True) -> ApiClient:
    """id (ac_…) или имя → клиент; AdminError — не найден или имя неоднозначно."""
    ref = " ".join((ref or "").split())
    if not ref:
        raise AdminError("Укажите имя клиента.")
    if ref.startswith("ac_"):
        client = await store.get_client(ref)
        matches = [client] if client is not None else []
    else:
        matches = [c for c in await store.list_clients() if c.name.lower() == ref.lower()]
    if active_only:
        active = [c for c in matches if c.active]
        if not active and matches:
            raise AdminError(f"Клиент «{matches[0].name}» отключён.")
        matches = active
    if not matches:
        raise AdminError(f"Клиент «{ref}» не найден. Список — /apikey list (или python -m api.keys list).")
    if len(matches) > 1:
        raise AdminError(f"Клиентов с именем «{ref}» несколько — укажите id: "
                         + ", ".join(c.id for c in matches))
    return matches[0]


async def _ensure_unique(name: str, except_id: str | None = None) -> None:
    for c in await store.list_clients():
        if c.active and c.name.lower() == name.lower() and c.id != except_id:
            raise AdminError(f"Клиент «{c.name}» уже есть. Новый ключ ему — rotate, другое имя — для нового.")


async def create(name: str, *, daily_limit: int = 100, watermark: bool = True, rate_limit_per_min: int = 120,
                 decks_per_min: int = 10) -> tuple[ApiClient, str]:
    """Новый клиент → (клиент, ключ). Ключ и client.webhook_secret показать один раз."""
    name = _name(name)
    await _ensure_unique(name)
    client, key = await store.create_client(name, daily_limit=_limit(daily_limit), watermark=watermark,
                                            rate_limit_per_min=rate_limit_per_min, decks_per_min=decks_per_min)
    logger.info("API client created", extra={"api_client_id": client.id, "daily_limit": client.daily_limit})
    return client, key


async def update(ref: str, *, name: str | None = None, daily_limit: int | None = None,
                 rate_limit_per_min: int | None = None, decks_per_min: int | None = None,
                 watermark: bool | None = None) -> ApiClient:
    client = await resolve(ref)
    if name is not None:
        name = _name(name)
        await _ensure_unique(name, except_id=client.id)
    if daily_limit is not None:
        _limit(daily_limit)
    updated = await store.update_client(client.id, name=name, daily_limit=daily_limit,
                                        rate_limit_per_min=rate_limit_per_min, decks_per_min=decks_per_min,
                                        watermark=watermark)
    logger.info("API client updated", extra={"api_client_id": client.id})
    return updated


async def set_limit(ref: str, daily_limit: int) -> ApiClient:
    return await update(ref, daily_limit=daily_limit)


async def set_watermark(ref: str, on: bool) -> ApiClient:
    return await update(ref, watermark=on)


async def revoke(ref: str) -> ApiClient:
    client = await resolve(ref)
    updated = await store.update_client(client.id, active=False)
    logger.info("API client revoked", extra={"api_client_id": client.id})
    return updated


async def rotate(ref: str) -> tuple[ApiClient, str]:
    """Новый ключ; старый перестаёт работать сразу."""
    client = await resolve(ref)
    key = await store.rotate_key(client.id)
    logger.info("API key rotated", extra={"api_client_id": client.id})
    return await store.get_client(client.id), key


async def overview(now: datetime | None = None, *, days: int = 30) -> list[ClientUsage]:
    """Все клиенты (сначала активные) с расходом за сутки UTC и за days дней."""
    now = now or datetime.now(timezone.utc)
    today = {r["client_id"]: r for r in await store.usage_report(store.day_start(now))}
    period = {r["client_id"]: r for r in await store.usage_report(now - timedelta(days=days))}
    out = []
    for c in await store.list_clients():
        t, p = today.get(c.id, {}), period.get(c.id, {})
        out.append(ClientUsage(
            client=c, used_today=t.get("decks", 0) - t.get("failed", 0), cost_today=t.get("cost_rub", 0.0),
            decks_30d=p.get("decks", 0), failed_30d=p.get("failed", 0), cost_30d=p.get("cost_rub", 0.0)))
    out.sort(key=lambda u: (not u.client.active, u.client.name.lower()))
    return out
