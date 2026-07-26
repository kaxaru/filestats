"""Вынужденные паузы при обходе каталога."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class PauseReason(enum.StrEnum):
    """Почему клиент ждёт.

    Перечисление вместо свободной строки: причина показывается пользователю,
    попадает в логи и хранится в базе. Свободный текст в такой роли неизбежно
    расходится по формулировкам и не поддаётся ни фильтрации, ни переводу.
    """

    rate_limited = "rate_limited"
    banned = "banned"
    network_error = "network_error"

    @property
    def message(self) -> str:
        return {
            PauseReason.rate_limited: "превышена допустимая частота запросов",
            PauseReason.banned: "клиент временно заблокирован сервером",
            PauseReason.network_error: "сеть недоступна",
        }[self]


@dataclass(frozen=True, slots=True)
class Pause:
    """Сколько и почему ждём."""

    reason: PauseReason
    seconds: float
    detail: str | None = None

    @property
    def message(self) -> str:
        if self.detail:
            return f"{self.reason.message}: {self.detail}"
        return self.reason.message
