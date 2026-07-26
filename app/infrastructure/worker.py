"""Фоновая задача скачивания.

Отдельный маленький объект, а не глобальная переменная с задачей: он же
обеспечивает единственность прогона. Два параллельных обхода с одного адреса
гарантированно упираются в лимит и получают получасовой бан, поэтому «запущено
не более одного» — требование, а не оптимизация.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class BackgroundWorker:
    def __init__(self, job: Job, *, name: str) -> None:
        self._job = job
        self._name = name
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def spawn(self) -> None:
        """Запустить задачу, если она ещё не идёт."""
        if self.is_running:
            logger.info("задача %s уже выполняется", self._name)
            return
        self._task = asyncio.create_task(self._guarded(), name=self._name)

    async def wait(self) -> None:
        """Дождаться завершения текущей задачи, если она есть."""
        if self._task is None:
            return
        await asyncio.gather(self._task, return_exceptions=True)

    async def stop(self) -> None:
        if not self.is_running or self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            logger.info("задача %s остановлена", self._name)

    async def _guarded(self) -> None:
        try:
            await self._job()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Сценарий уже пометил прогон неуспешным и записал причину.
            # Здесь остаётся не дать исключению утонуть в задаче без следа.
            logger.exception("задача %s завершилась ошибкой", self._name)
