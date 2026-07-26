"""initial schema

Revision ID: 8201cd8ca817
Revises:
Create Date: 2026-07-26 12:08:46.049780
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Колонки объявлены через собственные типы (FileNameType, FileContentType),
# и автогенерация ссылается на них по полному пути — без этого импорта
# сгенерированная миграция не выполнится.
import app.infrastructure.persistence.types

revision: str = "8201cd8ca817"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "failed",
                "stopped",
                name="run_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "pause_reason",
            sa.Enum(
                "rate_limited",
                "banned",
                "network_error",
                name="pause_reason",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("pause_detail", sa.String(length=1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_status", "download_runs", ["status"], unique=False)

    op.create_table(
        "files",
        sa.Column(
            "name", app.infrastructure.persistence.types.FileNameType(length=128), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "discovered",
                "downloaded",
                "marked",
                name="file_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("content", app.infrastructure.persistence.types.FileContentType(), nullable=True),
        *[
            sa.Column(f"d{digit}", sa.Integer(), server_default="0", nullable=False)
            for digit in range(10)
        ],
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_files_downloaded_at", "files", ["downloaded_at"], unique=False)
    op.create_index("ix_files_status", "files", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_files_status", table_name="files")
    op.drop_index("ix_files_downloaded_at", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_runs_status", table_name="download_runs")
    op.drop_table("download_runs")
