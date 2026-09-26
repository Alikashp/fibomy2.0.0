"""presentations: job_id, JSON презентации (spec), длительности этапов

ТЗ, раздел 8, п. 14: JSON презентации сохраняется в БД — для аналитики,
разбора жалоб и будущего переэкспорта.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("presentations", sa.Column("job_id", sa.String(32), nullable=True))
    op.add_column("presentations", sa.Column("spec", _json, nullable=True))
    op.add_column("presentations", sa.Column("durations_ms", _json, nullable=True))
    op.create_index("ix_presentations_job_id", "presentations", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_presentations_job_id", table_name="presentations")
    op.drop_column("presentations", "durations_ms")
    op.drop_column("presentations", "spec")
    op.drop_column("presentations", "job_id")
