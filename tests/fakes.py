"""Подставные реализации портов.

Сценарий обхода каталога зависит только от протоколов, поэтому проверяется
целиком — без базы, без сети и без ожиданий в реальном времени. Заодно
подставной клиент ведёт журнал вызовов: именно по нему проверяется главный
инвариант — подтверждение не может опередить сохранение.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.application.errors import FileMissingFromCatalog
from app.application.ports import MarkOutcome, PacingSnapshot
from app.domain.file import STATUSES_WITH_CONTENT, CatalogFile, FileStatus
from app.domain.run import DownloadRun
from app.domain.values import FileContent, FileName

START = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class StepClock:
    """Часы, двигающиеся на фиксированный шаг при каждом обращении."""

    def __init__(self, start: datetime = START, step: timedelta = timedelta(seconds=1)) -> None:
        self._now = start
        self._step = step

    def __call__(self) -> datetime:
        current = self._now
        self._now += self._step
        return current


@dataclass
class RecordedCall:
    kind: str
    names: tuple[str, ...] = ()


class FakeCatalogClient:
    """Каталог с заранее заданным содержимым.

    Выдаёт имена порциями, как настоящий: только те, что ещё не подтверждены,
    и в ограниченном количестве.
    """

    NAMES_PER_PORTION = 4

    def __init__(
        self,
        contents: dict[str, str],
        *,
        missing: set[str] | None = None,
        portion: int | None = None,
    ) -> None:
        self._contents = contents
        self._missing = missing or set()
        self._portion = portion or self.NAMES_PER_PORTION
        self._confirmed: set[str] = set()
        self.calls: list[RecordedCall] = []
        self.throttle_events = 0
        self.seconds_paused = 0.0
        self.interval_seconds = 1.0

    @property
    def confirmed(self) -> set[str]:
        return set(self._confirmed)

    async def fetch_names(self) -> list[FileName]:
        remaining = [name for name in self._contents if name not in self._confirmed]
        portion = remaining[: self._portion]
        self.calls.append(RecordedCall("names", tuple(portion)))
        return [FileName(name) for name in portion]

    async def fetch_contents(self, names: list[FileName]) -> dict[FileName, FileContent]:
        raw = tuple(name.value for name in names)
        self.calls.append(RecordedCall("download", raw))

        absent = [name for name in raw if name in self._missing]
        if absent and len(raw) == 1:
            raise FileMissingFromCatalog(list(absent))

        return {
            FileName(name): FileContent(self._contents[name])
            for name in raw
            if name not in self._missing
        }

    async def confirm_downloaded(self, names: list[FileName]) -> MarkOutcome:
        raw = tuple(name.value for name in names)
        self.calls.append(RecordedCall("confirm", raw))

        fresh = {name for name in raw if name not in self._confirmed}
        already = len(raw) - len(fresh)
        self._confirmed |= fresh
        return MarkOutcome(marked_now=len(fresh), already_marked=already)

    def pacing(self) -> PacingSnapshot:
        return PacingSnapshot(
            requests_made=len(self.calls),
            throttle_events=self.throttle_events,
            seconds_paused=self.seconds_paused,
            interval_seconds=self.interval_seconds,
        )

    def calls_of(self, kind: str) -> Iterator[RecordedCall]:
        return (call for call in self.calls if call.kind == kind)


class InMemoryFileRepository:
    def __init__(self, files: dict[str, CatalogFile] | None = None) -> None:
        self.files: dict[str, CatalogFile] = files or {}
        self.saved_names: list[tuple[str, ...]] = []

    async def register_discovered(self, names: list[FileName], now: datetime) -> None:
        for name in names:
            self.files.setdefault(name.value, CatalogFile.discover(name, now))

    async def names_without_content(self, names: list[FileName]) -> list[FileName]:
        return [
            name for name in names if self.files[name.value].status not in STATUSES_WITH_CONTENT
        ]

    async def get_many(self, names: list[FileName]) -> list[CatalogFile]:
        return [self.files[name.value] for name in names if name.value in self.files]

    async def save_many(self, files: list[CatalogFile]) -> None:
        for file in files:
            self.files[file.name.value] = file
        if files:
            self.saved_names.append(tuple(file.name.value for file in files))

    async def awaiting_confirmation(self, limit: int) -> list[CatalogFile]:
        pending = [file for file in self.files.values() if file.status is FileStatus.downloaded]
        return pending[:limit]

    async def count_awaiting_confirmation(self) -> int:
        return sum(1 for file in self.files.values() if file.status is FileStatus.downloaded)

    async def count_with_content(self) -> int:
        return sum(1 for file in self.files.values() if file.has_content)


class InMemoryRunRepository:
    def __init__(self) -> None:
        self.runs: list[DownloadRun] = []
        self._next_id = 1

    async def active(self) -> DownloadRun | None:
        return next((run for run in reversed(self.runs) if run.is_active), None)

    async def latest(self) -> DownloadRun | None:
        return self.runs[-1] if self.runs else None

    async def add(self, run: DownloadRun) -> DownloadRun:
        run.id = self._next_id
        self._next_id += 1
        self.runs.append(run)
        return run

    async def save(self, run: DownloadRun) -> None:
        if run not in self.runs:
            self.runs.append(run)


def catalog_of(count: int, *, digits: str = "0123456789") -> dict[str, str]:
    """Каталог из ``count`` файлов с предсказуемым содержимым."""
    return {f"file-{index:04d}.txt": (digits * 50)[:500] for index in range(count)}
