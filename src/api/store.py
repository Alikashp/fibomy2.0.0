"""БД для API: клиенты и их колоды (api_clients, decks). Без DATABASE_URL API не
работает — функции бросают StoreUnavailable (ответ 503)."""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.auth import hash_key, key_prefix, new_key, new_webhook_secret
from core.models.ids import ulid
from db.models import ApiClient, Deck
from db.session import get_session

logger = logging.getLogger(__name__)


class StoreUnavailable(RuntimeError):
    """БД не настроена (DATABASE_URL пуст)."""


def day_start(now: datetime) -> datetime:
    """Сутки лимита — календарные, UTC (00:00 UTC = 03:00 МСК)."""
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def next_day(now: datetime) -> datetime:
    return day_start(now) + timedelta(days=1)


def _require(session) -> None:
    if session is None:
        raise StoreUnavailable("DATABASE_URL is not set")


# ── Клиенты ─────────────────────────────────────────────────────────────────

async def client_by_key(key: str) -> ApiClient | None:
    async with get_session() as session:
        _require(session)
        row = await session.scalar(select(ApiClient).where(ApiClient.key_hash == hash_key(key)))
        return row if row is not None and row.active else None


async def get_client(client_id: str) -> ApiClient | None:
    async with get_session() as session:
        _require(session)
        return await session.get(ApiClient, client_id)


async def create_client(name: str, *, daily_limit: int = 100, rate_limit_per_min: int = 120,
                        decks_per_min: int = 10, watermark: bool = True) -> tuple[ApiClient, str]:
    """Новый клиент → (строка, ключ). Ключ больше нигде не хранится."""
    key = new_key()
    client = ApiClient(id=f"ac_{ulid()}", name=name, key_hash=hash_key(key), key_prefix=key_prefix(key),
                       webhook_secret=new_webhook_secret(), daily_limit=daily_limit,
                       rate_limit_per_min=rate_limit_per_min, decks_per_min=decks_per_min,
                       watermark=watermark, plan="free" if watermark else "pro", active=True,
                       created_at=datetime.now(timezone.utc))
    async with get_session() as session:
        _require(session)
        session.add(client)
    return client, key


async def rotate_key(client_id: str) -> str | None:
    """Новый ключ клиенту (старый перестаёт работать сразу) → ключ; None — нет клиента."""
    key = new_key()
    async with get_session() as session:
        _require(session)
        client = await session.get(ApiClient, client_id)
        if client is None:
            return None
        client.key_hash, client.key_prefix = hash_key(key), key_prefix(key)
    return key


async def update_client(client_id: str, **fields) -> ApiClient | None:
    allowed = {"name", "daily_limit", "rate_limit_per_min", "decks_per_min", "watermark", "active"}
    async with get_session() as session:
        _require(session)
        client = await session.get(ApiClient, client_id)
        if client is None:
            return None
        for name, value in fields.items():
            if name in allowed and value is not None:
                setattr(client, name, value)
        if fields.get("watermark") is not None:
            client.plan = "free" if client.watermark else "pro"
        if fields.get("active") is False:
            client.revoked_at = datetime.now(timezone.utc)
        return client


async def list_clients() -> list[ApiClient]:
    async with get_session() as session:
        _require(session)
        return list(await session.scalars(select(ApiClient).order_by(ApiClient.created_at)))


async def usage_report(since: datetime) -> list[dict]:
    """Колоды и себестоимость по клиентам с момента since (стоимость колоды — decks.cost_rub)."""
    async with get_session() as session:
        _require(session)
        rows = await session.execute(
            select(Deck.api_client_id, Deck.status, func.count(), func.coalesce(func.sum(Deck.cost_rub), 0))
            .where(Deck.api_client_id.is_not(None), Deck.created_at >= since)
            .group_by(Deck.api_client_id, Deck.status))
        report: dict[str, dict] = {}
        for client_id, status, count, cost in rows:
            item = report.setdefault(client_id, {"client_id": client_id, "decks": 0, "failed": 0, "cost_rub": 0.0})
            item["decks"] += count
            if status == "failed":
                item["failed"] += count
            item["cost_rub"] += float(cost or Decimal(0))
        return list(report.values())


# ── Колоды клиента ──────────────────────────────────────────────────────────

async def deck_for_client(client_id: str, deck_id: str) -> Deck | None:
    """Колода только своего клиента: чужая — как несуществующая."""
    async with get_session() as session:
        _require(session)
        deck = await session.get(Deck, deck_id)
        return deck if deck is not None and deck.api_client_id == client_id else None


async def deck_by_idempotency(client_id: str, key: str) -> Deck | None:
    async with get_session() as session:
        _require(session)
        return await session.scalar(select(Deck).where(Deck.api_client_id == client_id,
                                                       Deck.idempotency_key == key))


def _count_today_stmt(client_id: str, now: datetime):
    # В лимит идут все колоды суток, кроме упавших: ошибка не списывает генерацию (как в боте)
    return (select(func.count()).select_from(Deck)
            .where(Deck.api_client_id == client_id, Deck.created_at >= day_start(now), Deck.status != "failed"))


async def count_today(client_id: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        _require(session)
        return int(await session.scalar(_count_today_stmt(client_id, now)) or 0)


async def create_deck(client_id: str, deck_id: str, request: dict, *, idempotency_key: str | None,
                      daily_limit: int, now: datetime | None = None) -> tuple[str, Deck | None, int]:
    """Строка decks колоды API с проверкой суточного лимита → (исход, колода, колод суток до этой).

    Исход: "created" | "limit" (колода None) | "duplicate" — ключ идемпотентности уже занят
    параллельным запросом, колода — та, что создал он. Счёт и вставка — под блокировкой
    строки клиента (Postgres), чтобы два параллельных запроса не прошли лимит оба."""
    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        _require(session)
        await session.execute(select(ApiClient.id).where(ApiClient.id == client_id).with_for_update())
        used = int(await session.scalar(_count_today_stmt(client_id, now)) or 0)
        if used >= daily_limit:
            return "limit", None, used
        deck = Deck(id=deck_id, api_client_id=client_id, idempotency_key=idempotency_key, request=request,
                    status="queued", created_at=now)
        session.add(deck)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(Deck).where(Deck.api_client_id == client_id,
                                                               Deck.idempotency_key == idempotency_key))
            if existing is None:
                raise
            return "duplicate", existing, used
        return "created", deck, used


async def fail_deck(deck_id: str, code: str) -> None:
    async with get_session() as session:
        _require(session)
        deck = await session.get(Deck, deck_id)
        if deck is not None:
            deck.status, deck.error_code, deck.finished_at = "failed", code, datetime.now(timezone.utc)


async def ping() -> str:
    """Проверка БД для /v1/health: ok | error | disabled."""
    try:
        async with get_session() as session:
            if session is None:
                return "disabled"
            await session.execute(select(1))
            return "ok"
    except Exception as e:
        logger.warning("DB ping failed", extra={"error": str(e)})
        return "error"
