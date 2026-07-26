"""Показатели хода скачивания."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.application.ports import CatalogQueries, RunRepository
from app.domain.run import DownloadRun

Clock = Callable[[], datetime]

FULL_PERCENT = 100


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """Срез состояния процесса для интерфейса.

    ``names_discovered`` растёт по ходу работы: сколько всего файлов в каталоге,
    API не сообщает, поэтому знаменатель прогресса — не общее число файлов, а
    число уже увиденных имён.
    """

    run: DownloadRun | None
    names_discovered: int
    files_with_content: int
    pause_remaining_seconds: int | None

    @property
    def is_running(self) -> bool:
        return self.run is not None and self.run.is_active

    @property
    def percent(self) -> int:
        if not self.names_discovered:
            return 0
        return round(self.files_with_content / self.names_discovered * FULL_PERCENT)


class ProgressService:
    def __init__(self, runs: RunRepository, queries: CatalogQueries, clock: Clock) -> None:
        self._runs = runs
        self._queries = queries
        self._clock = clock

    async def snapshot(self) -> DownloadProgress:
        run = await self._runs.latest()
        return DownloadProgress(
            run=run,
            names_discovered=await self._queries.names_discovered(),
            files_with_content=await self._queries.files_with_content(),
            pause_remaining_seconds=run.pause_remaining(self._clock()) if run else None,
        )
