"""
Async SQLAlchemy session factory + репозиторий пользователей.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base, User, Presentation, PlanType

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Railway даёт postgres://, SQLAlchemy нужен postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = None
SessionLocal = None


async def init_db() -> None:
    """Инициализация БД при старте приложения."""
    global engine, SessionLocal

    if not DATABASE_URL:
        logger.warning("DATABASE_URL не задан — работаем без БД")
        return

    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )

    SessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        # Бот и воркер стартуют одновременно и оба зовут init_db — advisory
        # lock не даёт им применять миграции параллельно. Снимается на commit.
        if conn.dialect.name == "postgresql":
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        await conn.run_sync(_run_migrations)

    logger.info("Database initialized")


# ── Миграции (Alembic) ───────────────────────────────────────────────────────

_MIGRATION_LOCK_KEY = 7_140_512_001  # произвольная константа проекта
_BASELINE_REVISION = "0001"
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _run_migrations(sync_conn) -> None:
    """alembic upgrade head на переданном соединении.

    База на проде создана до Alembic (create_all + ALTER TABLE). Если таблицы
    есть, а alembic_version нет — это та самая база: догоняем её до baseline
    (те же идемпотентные ALTER, что были раньше) и помечаем baseline
    применённым, не выполняя его.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_INI.parent / "db" / "migrations"))
    cfg.attributes["connection"] = sync_conn

    tables = set(inspect(sync_conn).get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        logger.info("Legacy database without Alembic — stamping baseline", extra={"revision": _BASELINE_REVISION})
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS author_name VARCHAR(150)"))
        sync_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS author_group VARCHAR(150)"))
        command.stamp(cfg, _BASELINE_REVISION)

    command.upgrade(cfg, "head")


async def close_db() -> None:
    if engine:
        await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if SessionLocal is None:
        yield None
        return
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Репозиторий ────────────────────────────────────────────────────────────────

async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    language_code: str | None = None,
) -> User:
    """Возвращает пользователя или создаёт нового."""
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            user_id=user_id,
            username=username,
            first_name=first_name,
            language_code=language_code,
        )
        session.add(user)
        await session.flush()
        logger.info(f"New user: {user_id} @{username}")
    else:
        # Обновляем имя если изменилось
        if username and user.username != username:
            user.username = username
        if first_name and user.first_name != first_name:
            user.first_name = first_name
    return user


async def update_user_profile(
    session: AsyncSession,
    user: User,
    author_name: str | None,
    author_group: str | None,
) -> None:
    """Сохраняет ФИО/группу докладчика из профиля бота (main.py)."""
    user.author_name = author_name
    user.author_group = author_group
    await session.flush()


async def record_presentation(
    session: AsyncSession,
    user: User,
    topic: str,
    presentation_type: str,
    audience: str,
    language: str,
    slide_count: int | None = None,
    has_brief: bool = False,
    watermark: bool = True,
    job_id: str | None = None,
    spec: dict | None = None,
    durations_ms: dict | None = None,
) -> Presentation:
    """Записывает презентацию (вместе с её JSON) и увеличивает счётчик пользователя."""
    presentation = Presentation(
        user_id=user.user_id,
        topic=topic,
        presentation_type=presentation_type,
        audience=audience,
        language=language,
        slide_count=slide_count,
        has_brief=has_brief,
        watermark=watermark,
        job_id=job_id,
        spec=spec,
        durations_ms=durations_ms,
    )
    session.add(presentation)
    user.presentations_count += 1
    await session.flush()
    return presentation


async def upgrade_user_plan(
    session: AsyncSession,
    user: User,
    plan: PlanType,
    telegram_payment_charge_id: str,
    stars_amount: int,
) -> None:
    """Обновляет план после оплаты."""
    from db.models import Payment
    user.plan = plan.value if hasattr(plan, "value") else plan
    payment = Payment(
        user_id=user.user_id,
        telegram_payment_charge_id=telegram_payment_charge_id,
        plan=plan,
        stars_amount=stars_amount,
    )
    session.add(payment)
    await session.flush()
    logger.info(f"User {user.user_id} upgraded to {plan}, {stars_amount} stars")
