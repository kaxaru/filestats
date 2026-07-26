"""Агрегат «прогон скачивания»."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self

from app.domain.errors import InvalidTransition
from app.domain.pausing import Pause, PauseReason

SECONDS_IN_MINUTE = 60


class RunStatus(enum.StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"

    @property
    def is_terminal(self) -> bool:
        return self is not RunStatus.running


@dataclass(eq=False)
class DownloadRun:
    """Один запуск обхода каталога.

    Инварианты:

    1. Менять состояние можно только у активного прогона — терминальный статус
       окончателен.
    2. Пауза существует только у активного прогона: завершённый процесс ничего
       не ждёт.

    Живёт в хранилище, а не в памяти процесса: полный обход каталога идёт
    часами, и рестарт сервиса не должен его обнулять.
    """

    id: int | None = None
    status: RunStatus = RunStatus.running
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_activity_at: datetime | None = None
    paused_until: datetime | None = None
    pause_reason: PauseReason | None = None
    pause_detail: str | None = None
    error: str | None = None

    # Замеры темпа. Лежат в прогоне, а не в памяти процесса: обход переживает
    # рестарт, и счётчики обязаны пережить его вместе с ним — иначе итог
    # описывал бы только последний запуск.
    requests_made: int = 0
    throttle_events: int = 0
    seconds_paused: float = 0.0
    interval_seconds: float | None = None

    @classmethod
    def start(cls, now: datetime) -> Self:
        return cls(status=RunStatus.running, started_at=now, last_activity_at=now)

    @property
    def is_active(self) -> bool:
        return self.status is RunStatus.running

    def pause(self, pause: Pause, now: datetime) -> None:
        """Зафиксировать вынужденное ожидание — чтобы в интерфейсе была причина,
        а не молча висящий прогресс."""
        self._ensure_active()
        self.paused_until = now + timedelta(seconds=pause.seconds)
        self.pause_reason = pause.reason
        self.pause_detail = pause.detail

    def record_activity(self, now: datetime) -> None:
        self._ensure_active()
        self.last_activity_at = now
        self._clear_pause()

    @property
    def pause_message(self) -> str | None:
        if self.pause_reason is None:
            return None
        return Pause(self.pause_reason, 0, self.pause_detail).message

    def complete(self, now: datetime) -> None:
        self._finish(RunStatus.completed, now)

    def fail(self, error: str, now: datetime) -> None:
        self._finish(RunStatus.failed, now, error)

    def stop(self, now: datetime) -> None:
        self._finish(RunStatus.stopped, now, "процесс остановлен")

    def record_pacing(
        self, *, requests: int, throttles: int, paused: float, interval: float
    ) -> None:
        """Принять замеры темпа от клиента.

        Значения накопительные и заменяют предыдущие целиком: клиент считает
        их от начала своей работы, а не приращениями.
        """
        self.requests_made = requests
        self.throttle_events = throttles
        self.seconds_paused = paused
        self.interval_seconds = interval

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        finished = self.finished_at or self.last_activity_at
        return (finished - self.started_at).total_seconds() if finished else None

    @property
    def requests_per_minute(self) -> float | None:
        """Наблюдавшийся темп — то, что сервис реально выдержал.

        Это не заявленный лимит: каталог его не публикует. Это измерение —
        частота, при которой обход прошёл, и число отказов, которыми она
        оплачена.
        """
        elapsed = self.elapsed_seconds
        if not elapsed or not self.requests_made:
            return None
        return self.requests_made / elapsed * SECONDS_IN_MINUTE

    @property
    def has_pacing_data(self) -> bool:
        return self.requests_made > 0

    def pause_remaining(self, now: datetime) -> int | None:
        """Сколько секунд ещё ждать, или ``None``, если пауза неактуальна."""
        if not self.is_active or self.paused_until is None:
            return None
        remaining = (self.paused_until - now).total_seconds()
        return int(remaining) if remaining > 0 else None

    def _finish(self, status: RunStatus, now: datetime, error: str | None = None) -> None:
        self._ensure_active()
        self.status = status
        self.finished_at = now
        self.error = error
        self._clear_pause()

    def _clear_pause(self) -> None:
        self.paused_until = None
        self.pause_reason = None
        self.pause_detail = None

    def _ensure_active(self) -> None:
        if self.status.is_terminal:
            raise InvalidTransition(f"прогон уже завершён со статусом {self.status.value}")
