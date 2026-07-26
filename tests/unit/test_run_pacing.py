"""Замеры темпа на прогоне.

Каталог свои лимиты не публикует, поэтому судить о них можно только по
собственному трафику. Эти цифры и есть тот самый эмпирический вывод, поэтому
считаться они обязаны честно — в том числе через рестарт процесса.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.run import DownloadRun, RunStatus
from app.presentation.viewmodels import PacingViewModel, hint_for
from tests.fakes import START


def running() -> DownloadRun:
    return DownloadRun.start(START)


def test_hint_warns_when_nothing_was_downloaded() -> None:
    """Успешное завершение с нулём файлов должно объяснять себя."""
    assert "CANDIDATE_ID" in hint_for(RunStatus.completed, 0)
    assert "CANDIDATE_ID" not in hint_for(RunStatus.completed, 1)


def test_fresh_run_has_no_pacing_data() -> None:
    run = running()
    assert not run.has_pacing_data
    assert run.requests_per_minute is None
    assert PacingViewModel.build(run) is None


@pytest.mark.parametrize(
    ("requests", "elapsed_seconds", "expected_per_minute"),
    [
        (60, 60, 60.0),
        (30, 60, 30.0),
        (630, 667, 56.7),
        (1, 1, 60.0),
    ],
)
def test_rate_is_derived_from_elapsed_time(
    requests: int, elapsed_seconds: int, expected_per_minute: float
) -> None:
    run = running()
    run.record_pacing(requests=requests, throttles=0, paused=0.0, interval=1.0)
    run.complete(START + timedelta(seconds=elapsed_seconds))

    assert run.requests_per_minute == pytest.approx(expected_per_minute, abs=0.1)


def test_rate_is_unknown_without_elapsed_time() -> None:
    run = running()
    run.record_pacing(requests=10, throttles=0, paused=0.0, interval=1.0)
    # Ни завершения, ни отметки активности — считать не от чего.
    assert run.requests_per_minute is None


def test_pacing_is_replaced_not_accumulated() -> None:
    """Значения приходят накопительными — сценарий сам прибавляет базу."""
    run = running()
    run.record_pacing(requests=10, throttles=1, paused=5.0, interval=1.0)
    run.record_pacing(requests=25, throttles=2, paused=9.0, interval=1.5)

    assert run.requests_made == 25
    assert run.throttle_events == 2
    assert run.seconds_paused == 9.0
    assert run.interval_seconds == 1.5


def test_pacing_survives_completion() -> None:
    run = running()
    run.record_pacing(requests=630, throttles=1, paused=1.0, interval=1.04)
    run.complete(START + timedelta(minutes=11))

    assert run.has_pacing_data
    assert run.requests_made == 630


@pytest.mark.parametrize(
    ("throttles", "expected_fragment"),
    [
        (0, "Отказов не было"),
        (1, "1 отказ."),
        (2, "2 отказа."),
        (7, "7 отказов."),
        (12, "12 отказов."),
    ],
)
def test_verdict_reflects_whether_the_boundary_was_touched(
    throttles: int, expected_fragment: str
) -> None:
    run = running()
    run.record_pacing(requests=600, throttles=throttles, paused=0.0, interval=1.0)
    run.complete(START + timedelta(minutes=10))

    view = PacingViewModel.build(run)
    assert expected_fragment in view.verdict


@pytest.mark.parametrize(
    ("requests", "elapsed_minutes", "expected_fragment"),
    [
        (60, 1, "≈60 запросов"),
        (71, 1, "≈71 запрос"),
        (72, 1, "≈72 запроса"),
        (763, 11, "≈69 запросов"),
    ],
)
def test_verdict_agrees_with_the_number(
    requests: int, elapsed_minutes: int, expected_fragment: str
) -> None:
    """Числительное согласуется с числом — иначе текст читается как машинный."""
    run = running()
    run.record_pacing(requests=requests, throttles=1, paused=0.0, interval=1.0)
    run.complete(START + timedelta(minutes=elapsed_minutes))

    assert expected_fragment in PacingViewModel.build(run).verdict


def test_view_formats_measured_numbers() -> None:
    run = running()
    run.record_pacing(requests=630, throttles=1, paused=90.0, interval=1.04)
    run.complete(START + timedelta(seconds=667))

    view = PacingViewModel.build(run)
    assert view.requests_made == 630
    assert view.requests_per_minute == "57"
    assert view.throttle_events == 1
    assert view.interval_seconds == "1.04"
    assert view.minutes_paused == "1.5"


def test_elapsed_uses_last_activity_while_running() -> None:
    """У незавершённого прогона темп тоже должен считаться."""
    run = running()
    run.record_activity(START + timedelta(seconds=120))
    run.record_pacing(requests=120, throttles=0, paused=0.0, interval=1.0)

    assert run.requests_per_minute == pytest.approx(60.0)
