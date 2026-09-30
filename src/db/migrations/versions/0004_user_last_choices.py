"""users.last_*: последний выбор параметров в сводке перед генерацией

Короткий диалог (main.py, dialog.py): язык, число слайдов, аудитория и дизайн
не спрашиваются по очереди, а подставляются в сводку из прошлого выбора.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_language", sa.String(8), nullable=True))
    op.add_column("users", sa.Column("last_slide_count", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("last_audience", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("last_color_scheme", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_color_scheme")
    op.drop_column("users", "last_audience")
    op.drop_column("users", "last_slide_count")
    op.drop_column("users", "last_language")
