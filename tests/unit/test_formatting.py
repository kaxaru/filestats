"""Форматирование значений для интерфейса."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.presentation.formatting import EMPTY, MomentFormatter, percent, plural

NSK = "Asia/Novosibirsk"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "запросов"),
        (1, "запрос"),
        (2, "запроса"),
        (4, "запроса"),
        (5, "запросов"),
        (10, "запросов"),
        # Подростковые числа — исключение из общего правила.
        (11, "запросов"),
        (12, "запросов"),
        (14, "запросов"),
        (15, "запросов"),
        (21, "запрос"),
        (22, "запроса"),
        (25, "запросов"),
        (71, "запрос"),
        (101, "запрос"),
        (111, "запросов"),
        (112, "запросов"),
        (121, "запрос"),
        (763, "запроса"),
        (1234, "запроса"),
    ],
)
def test_plural_forms(count: int, expected: str) -> None:
    assert plural(count, "запрос", "запроса", "запросов") == expected


def test_moment_is_converted_to_display_timezone() -> None:
    """Хранится UTC, показывается НСК — это ровно +7 часов."""
    moment = datetime(2026, 7, 26, 12, 14, 31, tzinfo=UTC)
    assert MomentFormatter(NSK)(moment) == "26.07.2026 19:14:31"


def test_naive_moment_is_treated_as_utc() -> None:
    naive = datetime(2026, 7, 26, 12, 0, 0)
    assert MomentFormatter(NSK)(naive) == "26.07.2026 19:00:00"


def test_missing_moment_renders_a_dash() -> None:
    assert MomentFormatter(NSK)(None) == EMPTY


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, "0.00%"), (100.0, "100.00%"), (9.876, "9.88%"), (10.0, "10.00%")],
)
def test_percent(value: float, expected: str) -> None:
    assert percent(value) == expected
