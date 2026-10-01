"""api_clients, decks.warnings: REST API для второго бота (docs/design/04_CONTRACTS.md, 7, 8.3; D-055)

Клиент API — внешний бот или сервис с ключом. Ключ хранится хэшем SHA-256, сам
ключ показывается один раз при создании (python -m api.keys create). У клиента —
лимит генераций в сутки, частота запросов, водяной знак.

decks.api_client_id получает внешний ключ на api_clients (на SQLite — нет: ALTER
ограничений там не поддерживается, а SQLite — только тесты; на Postgres — NOT VALID). decks.warnings —
предупреждения колоды («материала хватило на 7 из 9»), их отдаёт статус API.

Только добавляющая миграция: откат кода без отката БД безопасен.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("webhook_secret", sa.String(64), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("decks_per_min", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("watermark", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("plan", sa.String(16), nullable=False, server_default="free"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_api_clients_key_hash", "api_clients", ["key_hash"], unique=True)
    op.add_column("decks", sa.Column("warnings", _json, nullable=True))
    op.create_index("ix_decks_client_created", "decks", ["api_client_id", "created_at"])
    if op.get_bind().dialect.name != "sqlite":
        # NOT VALID: проверяются только новые строки — накат после отката не падает на
        # колодах API, чьих клиентов откат удалил, и не держит блокировку на проверке decks
        op.create_foreign_key("fk_decks_api_client", "decks", "api_clients", ["api_client_id"], ["id"],
                              postgresql_not_valid=True)


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_decks_api_client", "decks", type_="foreignkey")
    op.drop_index("ix_decks_client_created", table_name="decks")
    op.drop_column("decks", "warnings")
    op.drop_index("uq_api_clients_key_hash", table_name="api_clients")
    op.drop_table("api_clients")
