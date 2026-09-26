"""
Окружение Alembic.

Два режима:
- из кода (db/session.py::init_db): соединение передаётся в
  config.attributes["connection"], миграции идут внутри уже открытой транзакции;
- из командной строки (cd src && alembic ...): создаём async-движок по DATABASE_URL.

logging.config.fileConfig намеренно не вызываем — логи настраивает logging_setup.
"""

import asyncio
import sys
from pathlib import Path

from alembic import context
from sqlalchemy.engine import Connection

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # src/

from db.models import Base  # noqa: E402

target_metadata = Base.metadata
config = context.config


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from db.session import DATABASE_URL

    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL не задан")
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline-режим (--sql) не поддерживается")

connection = config.attributes.get("connection")
if connection is not None:
    _do_run_migrations(connection)
else:
    asyncio.run(_run_async_migrations())
