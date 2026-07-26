"""Отображение домена на таблицы.

Маппинг императивный: доменные классы не наследуют базу SQLAlchemy и не знают
о существовании ORM. Схема хранения может меняться независимо от модели, а
доменные тесты обходятся без базы вообще.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import composite, registry

from app.domain.digits import DIGITS, DigitCounts
from app.domain.file import CatalogFile, FileStatus
from app.domain.pausing import PauseReason
from app.domain.run import DownloadRun, RunStatus
from app.infrastructure.persistence.types import FileContentType, FileNameType

STATUS_LENGTH = 16
PAUSE_REASON_LENGTH = 32
ERROR_LENGTH = 1024

mapper_registry = registry()
metadata: MetaData = mapper_registry.metadata


def _digit_columns() -> list[Column]:
    """Счётчики цифр отдельными колонками, а не JSON.

    Общая статистика по выбранным файлам должна считаться одним ``SUM`` на
    стороне базы; из JSON так не сложить.
    """
    return [Column(f"d{digit}", Integer, nullable=False, server_default="0") for digit in DIGITS]


files_table = Table(
    "files",
    metadata,
    Column("name", FileNameType, primary_key=True),
    Column(
        "status",
        Enum(FileStatus, name="file_status", native_enum=False, length=STATUS_LENGTH),
        nullable=False,
    ),
    Column("content", FileContentType, nullable=True),
    *_digit_columns(),
    Column("discovered_at", DateTime(timezone=True), nullable=False),
    Column("downloaded_at", DateTime(timezone=True), nullable=True),
    Column("marked_at", DateTime(timezone=True), nullable=True),
    # Список скачанных файлов сортируется по времени скачивания — это основной
    # запрос второй страницы.
    Index("ix_files_downloaded_at", "downloaded_at"),
    # Выбор неподтверждённых файлов выполняется в цикле скачивания постоянно.
    Index("ix_files_status", "status"),
)

runs_table = Table(
    "download_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "status",
        Enum(RunStatus, name="run_status", native_enum=False, length=STATUS_LENGTH),
        nullable=False,
    ),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("last_activity_at", DateTime(timezone=True), nullable=True),
    Column("paused_until", DateTime(timezone=True), nullable=True),
    Column(
        "pause_reason",
        Enum(PauseReason, name="pause_reason", native_enum=False, length=PAUSE_REASON_LENGTH),
        nullable=True,
    ),
    Column("pause_detail", String(ERROR_LENGTH), nullable=True),
    Column("error", Text, nullable=True),
    # Замеры темпа: лимиты каталог не публикует, судить о них можно только по
    # собственному трафику, поэтому он и записывается.
    Column("requests_made", Integer, nullable=False, server_default="0"),
    Column("throttle_events", Integer, nullable=False, server_default="0"),
    Column("seconds_paused", Float, nullable=False, server_default="0"),
    Column("interval_seconds", Float, nullable=True),
    Index("ix_runs_status", "status"),
)


def configure_mappings() -> None:
    """Связать доменные классы с таблицами. Вызывается один раз при старте."""
    if mapper_registry.mappers:
        return

    mapper_registry.map_imperatively(
        CatalogFile,
        files_table,
        properties={
            "counts": composite(DigitCounts, *[files_table.c[f"d{digit}"] for digit in DIGITS])
        },
    )
    mapper_registry.map_imperatively(DownloadRun, runs_table)


configure_mappings()
