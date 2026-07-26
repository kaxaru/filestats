"""run pacing measurements

Замеры темпа переезжают из памяти процесса в прогон: обход переживает рестарт,
и счётчики обязаны пережить его вместе с ним — иначе итог описывал бы только
последний запуск.

Revision ID: 56e39606f853
Revises: 8201cd8ca817
Create Date: 2026-07-26 13:20:47.912502
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "56e39606f853"
down_revision: str | None = "8201cd8ca817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "download_runs"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("requests_made", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        TABLE, sa.Column("throttle_events", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        TABLE, sa.Column("seconds_paused", sa.Float(), server_default="0", nullable=False)
    )
    op.add_column(TABLE, sa.Column("interval_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "interval_seconds")
    op.drop_column(TABLE, "seconds_paused")
    op.drop_column(TABLE, "throttle_events")
    op.drop_column(TABLE, "requests_made")
