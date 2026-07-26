"""Агрегат файла: односторонние переходы и защита от потери данных."""

from __future__ import annotations

import pytest

from app.domain.errors import ContentRequired, InvalidTransition
from app.domain.file import CatalogFile, FileStatus
from app.domain.values import FileContent, FileName
from tests.fakes import START

NAME = FileName("sample.txt")
CONTENT = FileContent("1234512345")


def discovered() -> CatalogFile:
    return CatalogFile.discover(NAME, START)


def downloaded() -> CatalogFile:
    file = discovered()
    file.attach_content(CONTENT, START)
    return file


def confirmed() -> CatalogFile:
    file = downloaded()
    file.confirm_marked(START)
    return file


def test_discovered_file_has_no_content() -> None:
    file = discovered()
    assert file.status is FileStatus.discovered
    assert not file.has_content
    assert file.counts.total == 0


def test_attaching_content_computes_counts_in_the_same_step() -> None:
    """Счётчики не могут разойтись с содержимым — они считаются только здесь."""
    file = downloaded()
    assert file.status is FileStatus.downloaded
    assert file.counts.as_dict()["1"] == 2
    assert file.counts.total == len(CONTENT.raw)


@pytest.mark.parametrize(
    ("factory", "allowed"),
    [
        (discovered, True),
        (downloaded, True),
        (confirmed, False),
    ],
    ids=["discovered", "downloaded", "marked"],
)
def test_content_can_be_attached_until_confirmation(factory, allowed: bool) -> None:
    file = factory()
    if allowed:
        file.attach_content(CONTENT, START)
        assert file.status is FileStatus.downloaded
    else:
        with pytest.raises(InvalidTransition):
            file.attach_content(CONTENT, START)


def test_confirmation_without_content_is_refused() -> None:
    """Главный инвариант: отметка необратима, отмечать нечего — значит потеря."""
    with pytest.raises(ContentRequired):
        discovered().confirm_marked(START)


def test_confirmation_is_final() -> None:
    file = confirmed()
    assert file.is_confirmed
    with pytest.raises(InvalidTransition):
        file.confirm_marked(START)


def test_timestamps_are_recorded_per_transition() -> None:
    file = discovered()
    assert file.discovered_at == START
    assert file.downloaded_at is None

    file.attach_content(CONTENT, START)
    assert file.downloaded_at == START
    assert file.marked_at is None

    file.confirm_marked(START)
    assert file.marked_at == START
