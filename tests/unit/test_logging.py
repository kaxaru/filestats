"""Структурные логи."""

from __future__ import annotations

import json
import logging

import pytest

from app.infrastructure.logging import JsonFormatter


def record(level: int = logging.INFO, message: str = "сообщение", **extra) -> logging.LogRecord:
    made = logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    made.__dict__.update(extra)
    return made


def formatted(**kwargs) -> dict:
    return json.loads(JsonFormatter().format(record(**kwargs)))


def test_base_fields_are_present() -> None:
    payload = formatted()
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "сообщение"
    assert payload["ts"].endswith("+00:00")


@pytest.mark.parametrize(
    "level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
)
def test_every_level_is_rendered(level: int) -> None:
    assert formatted(level=level)["level"] == logging.getLevelName(level)


@pytest.mark.parametrize(
    "extra",
    [
        {"run_id": 7},
        {"event": "run.completed", "files_saved": 120},
        {"names": ["a.txt", "b.txt"]},
        {"minutes_paused": 3.5},
    ],
)
def test_extra_fields_become_top_level_keys(extra: dict) -> None:
    """Ради этого структурные логи и заводятся — иначе всё тонет в тексте."""
    payload = formatted(**extra)
    for key, value in extra.items():
        assert payload[key] == value


def test_cyrillic_is_not_escaped() -> None:
    assert "сообщение" in JsonFormatter().format(record())


def test_output_is_a_single_line() -> None:
    line = JsonFormatter().format(record(message="первая\nвторая"))
    assert "\n" not in line
    assert json.loads(line)["message"] == "первая\nвторая"


def test_exception_is_captured() -> None:
    try:
        raise ValueError("сломалось")
    except ValueError:
        import sys

        made = record()
        made.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(made))

    assert "ValueError: сломалось" in payload["exception"]


def test_unserialisable_values_do_not_break_logging() -> None:
    """Лог не должен падать из-за того, что в него положили странный объект."""
    payload = formatted(weird=object())
    assert "weird" in payload
