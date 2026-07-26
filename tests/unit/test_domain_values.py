"""Объекты-значения: проверки, ради которых они и заведены."""

from __future__ import annotations

import pytest

from app.domain.digits import DigitCounts
from app.domain.values import (
    CANONICAL_CONTENT_LENGTH,
    FileContent,
    FileName,
    InvalidFileName,
)


@pytest.mark.parametrize(
    "raw",
    [
        "000d7d0a-acef-4c95-b92d-1aa496b1858a.txt",
        "a.txt",
        "file_1-2.3.txt",
        "A0",
    ],
)
def test_valid_names_are_accepted(raw: str) -> None:
    assert FileName(raw).value == raw


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("", "пустое имя"),
        ("../../etc/passwd", "выход за пределы каталога"),
        ("dir/file.txt", "путь вместо имени"),
        ("dir\\file.txt", "путь в windows-нотации"),
        ("..", "родительский каталог"),
        (".hidden", "имя не может начинаться с точки"),
        ("файл.txt", "непечатаемые в ASCII символы"),
        ("a" * 200, "слишком длинное имя"),
    ],
)
def test_dangerous_names_are_rejected(raw: str, why: str) -> None:
    with pytest.raises(InvalidFileName):
        FileName(raw)


def test_names_are_comparable_and_hashable() -> None:
    first, second = FileName("a.txt"), FileName("b.txt")
    assert first < second
    assert len({FileName("a.txt"), first}) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  123  ", "123"), ("\n456\n", "456"), ("789", "789")],
)
def test_content_is_trimmed(raw: str, expected: str) -> None:
    assert FileContent.parse(raw).raw == expected


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("1" * CANONICAL_CONTENT_LENGTH, True),
        ("1" * (CANONICAL_CONTENT_LENGTH - 1), False),
        ("1" * (CANONICAL_CONTENT_LENGTH + 1), False),
        ("x" * CANONICAL_CONTENT_LENGTH, False),
        ("", False),
    ],
)
def test_canonical_content_is_recognised(raw: str, canonical: bool) -> None:
    assert FileContent(raw).is_canonical is canonical


def test_non_canonical_content_is_still_kept() -> None:
    """Отвергать уже скачанное нельзя — это потеря данных, а не защита."""
    content = FileContent.parse("12ab34")
    assert not content.is_canonical
    assert content.raw == "12ab34"
    assert content.digit_counts() == DigitCounts(d1=1, d2=1, d3=1, d4=1)
