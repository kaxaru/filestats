"""Подбор темпа и разбор ``Retry-After``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from app.config import PacingSettings
from app.infrastructure.catalog_api.pacing import AdaptivePacer, parse_retry_after

FALLBACK = 7.0


@pytest.fixture
def pacing() -> PacingSettings:
    return PacingSettings(
        initial_interval_seconds=1.0,
        min_interval_seconds=0.25,
        max_interval_seconds=16.0,
        speedup_after_successes=3,
        speedup_factor=0.5,
        backoff_factor=2.0,
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("30", 30.0),
        ("0", 0.0),
        ("1800", 1800.0),
        ("  45  ", 45.0),
        ("12.5", 12.5),
    ],
)
def test_numeric_retry_after(header: str, expected: float) -> None:
    assert parse_retry_after(header, fallback=FALLBACK) == expected


@pytest.mark.parametrize("offset_seconds", [10, 60, 1800])
def test_http_date_retry_after(offset_seconds: int) -> None:
    moment = datetime.now(UTC) + timedelta(seconds=offset_seconds)
    parsed = parse_retry_after(format_datetime(moment), fallback=FALLBACK)
    assert parsed == pytest.approx(offset_seconds, abs=5)


@pytest.mark.parametrize("header", [None, "", "завтра", "not-a-date", "-"])
def test_unparsable_retry_after_falls_back(header: str | None) -> None:
    assert parse_retry_after(header, fallback=FALLBACK) == FALLBACK


@pytest.mark.parametrize("seconds_ago", [1, 120, 100000])
def test_past_date_means_no_wait(seconds_ago: int) -> None:
    past = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    assert parse_retry_after(format_datetime(past), fallback=FALLBACK) == 0.0


def test_negative_seconds_are_clamped() -> None:
    assert parse_retry_after("-30", fallback=FALLBACK) == 0.0


def test_speeds_up_only_after_a_streak(pacing: PacingSettings) -> None:
    pacer = AdaptivePacer(pacing)

    for _ in range(pacing.speedup_after_successes - 1):
        pacer.on_success()
    assert pacer.interval == pacing.initial_interval_seconds

    pacer.on_success()
    assert pacer.interval == pytest.approx(pacing.initial_interval_seconds * pacing.speedup_factor)


def test_backs_off_immediately(pacing: PacingSettings) -> None:
    """Торможение резкое: лишний запрос стоит получаса бана."""
    pacer = AdaptivePacer(pacing)
    pacer.on_throttled(retry_after_seconds=10)
    assert pacer.interval == pytest.approx(pacing.initial_interval_seconds * pacing.backoff_factor)


def test_one_refusal_undoes_several_speedups(pacing: PacingSettings) -> None:
    pacer = AdaptivePacer(pacing)
    for _ in range(pacing.speedup_after_successes * 2):
        pacer.on_success()
    accelerated = pacer.interval

    pacer.on_throttled(retry_after_seconds=1)
    assert pacer.interval > accelerated


@pytest.mark.parametrize("successes", [10, 100, 1000])
def test_never_faster_than_the_floor(pacing: PacingSettings, successes: int) -> None:
    pacer = AdaptivePacer(pacing)
    for _ in range(successes):
        pacer.on_success()
    assert pacer.interval >= pacing.min_interval_seconds


@pytest.mark.parametrize("refusals", [5, 50, 500])
def test_never_slower_than_the_ceiling(pacing: PacingSettings, refusals: int) -> None:
    pacer = AdaptivePacer(pacing)
    for _ in range(refusals):
        pacer.on_throttled(retry_after_seconds=1)
    assert pacer.interval <= pacing.max_interval_seconds


async def test_acquire_does_not_block_before_first_request(pacing: PacingSettings) -> None:
    await AdaptivePacer(pacing).acquire()
