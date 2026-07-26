"""Подбор темпа запросов к каталогу."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import PacingSettings

#: Доля от паузы, добавляемая случайным разбросом. Без него запросы
#: выстраиваются в ровную сетку и синхронно бьются в границу окна лимитера.
JITTER_FRACTION = 0.1

#: Минимальная база для расчёта разброса, чтобы джиттер не вырождался в ноль.
MIN_JITTER_BASE_SECONDS = 0.1


def parse_retry_after(value: str | None, *, fallback: float) -> float:
    """Разобрать ``Retry-After``: и число секунд, и HTTP-дату.

    Спецификация допускает оба формата, а сервер про свой выбор не сообщает,
    поэтому поддержаны оба. Отрицательное время ожидания смысла не имеет —
    просроченная дата означает «уже можно».
    """
    if not value:
        return fallback

    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return fallback

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


class AdaptivePacer:
    """Интервал между запросами, подбираемый вслепую.

    Лимиты каталога нигде не опубликованы, поэтому единственный источник
    сведений о них — отказы. Ускорение осторожное и постепенное, торможение
    немедленное и кратное: лишний запрос стоит получаса бана, лишняя пауза —
    доли секунды.
    """

    def __init__(self, settings: PacingSettings) -> None:
        self._settings = settings
        self._interval = settings.initial_interval_seconds
        self._successes = 0
        self._next_allowed_at = 0.0

    @property
    def interval(self) -> float:
        return self._interval

    async def acquire(self) -> None:
        delay = self._next_allowed_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def on_success(self) -> None:
        self._successes += 1
        if self._successes >= self._settings.speedup_after_successes:
            self._successes = 0
            self._interval = max(
                self._settings.min_interval_seconds,
                self._interval * self._settings.speedup_factor,
            )
        self._arm(self._interval)

    def on_throttled(self, retry_after_seconds: float) -> None:
        self._successes = 0
        self._interval = min(
            self._settings.max_interval_seconds,
            max(
                self._interval * self._settings.backoff_factor,
                self._settings.initial_interval_seconds,
            ),
        )
        self._arm(retry_after_seconds + self._settings.retry_after_padding_seconds)

    def _arm(self, delay: float) -> None:
        base = max(delay, MIN_JITTER_BASE_SECONDS)
        self._next_allowed_at = time.monotonic() + delay + random.uniform(0, JITTER_FRACTION * base)
