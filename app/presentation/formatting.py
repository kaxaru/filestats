"""Форматирование значений для отображения."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

EMPTY = "—"
DATETIME_FORMAT = "%d.%m.%Y %H:%M:%S"
PERCENT_FORMAT = "{:.2f}%"


class MomentFormatter:
    """Переводит моменты времени в часовой пояс отображения.

    В хранилище всё лежит в UTC — часовой пояс относится к представлению и
    только к нему. Новосибирск задан настройкой, а не зашит в код: показывать
    время в НСК требует задание, но это всё ещё решение уровня интерфейса.
    """

    def __init__(self, timezone_name: str) -> None:
        self._zone = ZoneInfo(timezone_name)

    def __call__(self, moment: datetime | None) -> str:
        if moment is None:
            return EMPTY
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(self._zone).strftime(DATETIME_FORMAT)


def percent(value: float) -> str:
    return PERCENT_FORMAT.format(value)


TEEN_RANGE = range(11, 15)
FEW_RANGE = range(2, 5)


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русская форма существительного при числительном.

    «71 запрос», но «72 запроса» и «75 запросов». Без этого показатели в
    интерфейсе читаются как машинный перевод.
    """
    if count % 100 in TEEN_RANGE:
        return many
    remainder = count % 10
    if remainder == 1:
        return one
    if remainder in FEW_RANGE:
        return few
    return many
