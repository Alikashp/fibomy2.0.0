"""baseline: схема на момент перехода на Alembic

Совпадает с тем, что раньше создавали create_all + ALTER TABLE в init_db.
На существующей базе эта миграция не выполняется: init_db помечает её
применённой (alembic stamp), см. db/session.py::_run_migrations.

Revision ID: 0001
Revises:
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=True),
        sa.Column("language_code", sa.String(8), nullable=True),
        sa.Column("plan", sa.String(16), nullable=False, server_default="free"),
        sa.Column("presentations_count", sa.Integer(), nullable=False),
        sa.Column("author_name", sa.String(150), nullable=True),
        sa.Column("author_group", sa.String(150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "presentations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("presentation_type", sa.String(32), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("has_brief", sa.Boolean(), nullable=False),
        sa.Column("watermark", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(256), nullable=False, unique=True),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("presentations")
    op.drop_table("users")
