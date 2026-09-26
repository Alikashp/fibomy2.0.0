"""users.source_mode: запомненный выбор «только мой материал» / «дополнить общими знаниями»

ТЗ 3.2: вопрос о режиме работы с материалом задаётся, только если пользователь
приложил файл или текст; выбор запоминается в профиле и подставляется по
умолчанию. Значения — schemas.presentation.SourceMode (strict | extend).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("source_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "source_mode")
