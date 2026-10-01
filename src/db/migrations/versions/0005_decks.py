"""decks, deck_revisions: колоды нового движка (docs/design/04_CONTRACTS.md, 8; D-034)

Бот создаёт строку decks с параметрами (DeckRequest) и ставит в очередь только
deck_id. Воркер пишет статус, этап, DeckSpec, длительности, токены, стоимость.
deck_revisions пишется с фазы 4 (правки), создаётся сразу. Старые таблицы не
меняются — откат кода без отката БД безопасен (08_MIGRATION.md, 1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("api_client_id", sa.String(32), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("parent_deck_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(16), nullable=True),
        sa.Column("request", _json, nullable=False),
        sa.Column("spec", _json, nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files", _json, nullable=True),
        sa.Column("digest_key", sa.String(256), nullable=True),
        sa.Column("durations_ms", _json, nullable=True),
        sa.Column("usage", _json, nullable=True),
        sa.Column("cost_rub", sa.Numeric(10, 4), nullable=True),
        sa.Column("degradations", _json, nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("counted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_decks_user_created", "decks", ["user_id", sa.text("created_at DESC")])
    op.create_index("ix_decks_status_started", "decks", ["status", "started_at"])
    op.create_index("uq_decks_client_idempotency", "decks", ["api_client_id", "idempotency_key"], unique=True)
    op.create_table(
        "deck_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("deck_id", sa.String(32), sa.ForeignKey("decks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("slide_id", sa.String(8), nullable=True),
        sa.Column("spec", _json, nullable=False),
        sa.Column("cost_rub", sa.Numeric(10, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deck_revisions_deck", "deck_revisions", ["deck_id", "revision"])


def downgrade() -> None:
    op.drop_index("ix_deck_revisions_deck", table_name="deck_revisions")
    op.drop_table("deck_revisions")
    op.drop_index("uq_decks_client_idempotency", table_name="decks")
    op.drop_index("ix_decks_status_started", table_name="decks")
    op.drop_index("ix_decks_user_created", table_name="decks")
    op.drop_table("decks")
