"""Агрегат прогона: терминальные статусы окончательны."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.errors import InvalidTransition
from app.domain.pausing import Pause, PauseReason
from app.domain.run import DownloadRun, RunStatus
from tests.fakes import START


def running() -> DownloadRun:
    return DownloadRun.start(START)


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        (RunStatus.running, False),
        (RunStatus.completed, True),
        (RunStatus.failed, True),
        (RunStatus.stopped, True),
    ],
)
def test_terminality(status: RunStatus, terminal: bool) -> None:
    assert status.is_terminal is terminal


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        (lambda run: run.complete(START), RunStatus.completed),
        (lambda run: run.fail("сломалось", START), RunStatus.failed),
        (lambda run: run.stop(START), RunStatus.stopped),
    ],
    ids=["completed", "failed", "stopped"],
)
def test_finishing(finish, expected: RunStatus) -> None:
    run = running()
    finish(run)
    assert run.status is expected
    assert run.finished_at == START
    assert not run.is_active


@pytest.mark.parametrize(
    "act",
    [
        lambda run: run.complete(START),
        lambda run: run.fail("ещё раз", START),
        lambda run: run.stop(START),
        lambda run: run.record_activity(START),
        lambda run: run.pause(Pause(PauseReason.rate_limited, 10), START),
    ],
    ids=["complete", "fail", "stop", "activity", "pause"],
)
def test_finished_run_rejects_further_changes(act) -> None:
    run = running()
    run.complete(START)
    with pytest.raises(InvalidTransition):
        act(run)


def test_pause_is_recorded_with_reason() -> None:
    run = running()
    run.pause(Pause(PauseReason.rate_limited, seconds=30), START)

    assert run.paused_until == START + timedelta(seconds=30)
    assert run.pause_reason is PauseReason.rate_limited
    assert run.pause_message == PauseReason.rate_limited.message


def test_pause_detail_is_appended_to_message() -> None:
    run = running()
    run.pause(Pause(PauseReason.network_error, seconds=4, detail="таймаут"), START)
    assert run.pause_message == "сеть недоступна: таймаут"


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected"),
    [(0, 30), (10, 20), (30, None), (60, None)],
)
def test_pause_remaining(elapsed_seconds: int, expected: int | None) -> None:
    run = running()
    run.pause(Pause(PauseReason.banned, seconds=30), START)
    assert run.pause_remaining(START + timedelta(seconds=elapsed_seconds)) == expected


def test_activity_clears_pause() -> None:
    run = running()
    run.pause(Pause(PauseReason.banned, seconds=1800), START)
    run.record_activity(START)

    assert run.paused_until is None
    assert run.pause_reason is None
    assert run.pause_remaining(START) is None


def test_finished_run_has_no_pause() -> None:
    run = running()
    run.pause(Pause(PauseReason.rate_limited, seconds=60), START)
    run.complete(START)
    assert run.paused_until is None
