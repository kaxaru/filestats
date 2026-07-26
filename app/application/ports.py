"""Порты приложения.

Границы, через которые прикладной слой общается с внешним миром. Сценарии
зависят только от этих протоколов, поэтому проверяются подставными реализациями
— без базы, без сети и без ожиданий в реальном времени.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.digits import DigitCounts
from app.domain.file import CatalogFile
from app.domain.pausing import Pause
from app.domain.run import DownloadRun
from app.domain.selection import Selection
from app.domain.sorting import Sorting
from app.domain.values import FileContent, FileName


@dataclass(frozen=True, slots=True)
class MarkOutcome:
    """Ответ каталога на отметку файлов."""

    marked_now: int
    already_marked: int


@dataclass(frozen=True, slots=True)
class PacingSnapshot:
    """Замеры темпа обращения к каталогу.

    Лимиты каталог не публикует, поэтому единственный способ о них судить —
    измерять собственный трафик: с какой частотой шли запросы и сколько раз
    она оказалась чрезмерной.
    """

    requests_made: int
    throttle_events: int
    seconds_paused: float
    interval_seconds: float

    @classmethod
    def zero(cls) -> PacingSnapshot:
        return cls(requests_made=0, throttle_events=0, seconds_paused=0.0, interval_seconds=0.0)


@dataclass(frozen=True, slots=True)
class FileDigits:
    """Строка отчёта «статистика по файлам»."""

    name: FileName
    downloaded_at: datetime | None
    counts: DigitCounts


@dataclass(frozen=True, slots=True)
class FilePage:
    """Страница списка скачанных файлов."""

    records: tuple[CatalogFile, ...]
    total: int
    number: int
    pages: int

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


class ThrottleObserver(Protocol):
    """Уведомление о вынужденном ожидании — чтобы пауза была видна в интерфейсе."""

    async def on_wait(self, pause: Pause) -> None: ...


class CatalogClient(Protocol):
    """Внешнее API каталога."""

    async def fetch_names(self) -> list[FileName]:
        """Порция имён. Пустой список означает, что каталог скачан полностью."""

    async def fetch_contents(self, names: list[FileName]) -> dict[FileName, FileContent]:
        """Содержимое файлов по именам."""

    async def confirm_downloaded(self, names: list[FileName]) -> MarkOutcome:
        """Отметить файлы скачанными. Операция идемпотентна."""

    def pacing(self) -> PacingSnapshot:
        """Накопленные замеры темпа с начала работы клиента."""


class FileRepository(Protocol):
    async def register_discovered(self, names: list[FileName], now: datetime) -> None:
        """Запомнить полученные имена, не трогая уже известные."""

    async def names_without_content(self, names: list[FileName]) -> list[FileName]:
        """Из переданных имён — те, содержимого которых у нас ещё нет."""

    async def get_many(self, names: list[FileName]) -> list[CatalogFile]: ...

    async def save_many(self, files: list[CatalogFile]) -> None: ...

    async def awaiting_confirmation(self, limit: int) -> list[CatalogFile]:
        """Файлы, сохранённые у нас, но не подтверждённые сервером."""

    async def count_awaiting_confirmation(self) -> int: ...

    async def count_with_content(self) -> int:
        """Сколько файлов скачано — достоверный итог для строки завершения."""


class RunRepository(Protocol):
    async def active(self) -> DownloadRun | None: ...

    async def latest(self) -> DownloadRun | None: ...

    async def add(self, run: DownloadRun) -> DownloadRun: ...

    async def save(self, run: DownloadRun) -> None: ...


class CatalogQueries(Protocol):
    """Чтение для интерфейса.

    Отделено от репозиториев намеренно: страницам нужны срезы и агрегаты, а не
    агрегаты предметной области целиком. Тянуть тысячи объектов, чтобы сложить
    десять чисел, — работа для SQL, а не для домена.
    """

    async def names_discovered(self) -> int: ...

    async def files_with_content(self) -> int: ...

    async def page(self, *, number: int, size: int, sorting: Sorting) -> FilePage: ...

    async def count_selected(self, selection: Selection) -> int: ...

    async def digit_totals(self, selection: Selection) -> DigitCounts: ...

    async def per_file_digits(
        self, selection: Selection, *, sorting: Sorting, limit: int, offset: int = 0
    ) -> list[FileDigits]: ...
