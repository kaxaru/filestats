"""Сценарий запуска прогона.

Отделён от обхода каталога намеренно: запуск не обращается к внешнему API и
зависит только от хранилища прогонов, тогда как обходу нужен клиент каталога.
Держать их вместе означало бы конструировать HTTP-клиент ради того, чтобы
вставить строку в таблицу.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.ports import RunRepository
from app.domain.run import DownloadRun

Clock = Callable[[], datetime]


class DownloadLauncher:
    def __init__(self, runs: RunRepository, clock: Clock) -> None:
        self._runs = runs
        self._clock = clock

    async def begin(self) -> DownloadRun:
        """Начать прогон или вернуть уже идущий.

        Повторное нажатие кнопки не должно плодить параллельные обходы: лимит
        частоты считается по адресу, и второй обход лишь приблизит бан.
        """
        existing = await self._runs.active()
        if existing is not None:
            return existing
        return await self._runs.add(DownloadRun.start(self._clock()))
