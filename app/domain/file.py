"""Агрегат «файл каталога»."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self

from app.domain.digits import DigitCounts
from app.domain.errors import ContentRequired, InvalidTransition
from app.domain.values import FileContent, FileName


class FileStatus(enum.StrEnum):
    """Жизненный цикл файла.

    Различие между ``downloaded`` и ``marked`` — не формальность, а суть защиты
    от потери данных:

    * ``discovered`` — имя получено, содержимого ещё нет;
    * ``downloaded`` — содержимое лежит у нас, серверу мы об этом не сообщали;
    * ``marked``     — сервер подтвердил отметку и это имя больше не выдаст.
    """

    discovered = "discovered"
    downloaded = "downloaded"
    marked = "marked"


#: Статусы, при которых содержимое файла у нас есть. Знание доменное, а не
#: инфраструктурное: им пользуются и репозиторий, и запросы чтения, и держать
#: его в двух местах — гарантированно разойтись при первой же правке.
STATUSES_WITH_CONTENT: tuple[FileStatus, ...] = (FileStatus.downloaded, FileStatus.marked)


# Переходы односторонние: назад из marked дороги нет, потому что на стороне
# сервера отметка необратима.
_ALLOWED: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.discovered: frozenset({FileStatus.downloaded}),
    FileStatus.downloaded: frozenset({FileStatus.downloaded, FileStatus.marked}),
    FileStatus.marked: frozenset(),
}


@dataclass(eq=False)
class CatalogFile:
    """Файл каталога.

    Инварианты, за которые отвечает агрегат:

    1. Содержимое и счётчики цифр не расходятся — счётчики пересчитываются
       ровно там, где присваивается содержимое, и больше нигде.
    2. Подтвердить отметку можно только для файла с сохранённым содержимым.
    3. Статус движется только вперёд.
    """

    name: FileName
    status: FileStatus = FileStatus.discovered
    content: FileContent | None = None
    counts: DigitCounts = field(default_factory=DigitCounts.zero)
    discovered_at: datetime | None = None
    downloaded_at: datetime | None = None
    marked_at: datetime | None = None

    @classmethod
    def discover(cls, name: FileName, now: datetime) -> Self:
        return cls(name=name, status=FileStatus.discovered, discovered_at=now)

    @property
    def has_content(self) -> bool:
        return self.content is not None

    @property
    def is_confirmed(self) -> bool:
        return self.status is FileStatus.marked

    def attach_content(self, content: FileContent, now: datetime) -> None:
        """Сохранить содержимое: ``discovered`` → ``downloaded``.

        Счётчики считаются здесь же — это единственная точка, где содержимое
        попадает в агрегат, поэтому разойтись они не могут.
        """
        self._ensure_transition(FileStatus.downloaded)
        self.content = content
        self.counts = content.digit_counts()
        self.status = FileStatus.downloaded
        self.downloaded_at = now

    def confirm_marked(self, now: datetime) -> None:
        """Зафиксировать подтверждение сервера: ``downloaded`` → ``marked``."""
        if not self.has_content:
            raise ContentRequired(
                f"{self.name}: отметка без сохранённого содержимого потеряет файл навсегда"
            )
        self._ensure_transition(FileStatus.marked)
        self.status = FileStatus.marked
        self.marked_at = now

    def _ensure_transition(self, target: FileStatus) -> None:
        if target not in _ALLOWED[self.status]:
            raise InvalidTransition(f"{self.name}: {self.status.value} → {target.value} запрещён")
