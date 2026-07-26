"""Счётчики цифр."""

from __future__ import annotations

import pytest

from app.domain.digits import DIGITS, DigitCounts


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", {}),
        ("0", {"0": 1}),
        ("1234567890", dict.fromkeys(DIGITS, 1)),
        ("111", {"1": 3}),
        ("2395778969", {"2": 1, "3": 1, "9": 3, "5": 1, "7": 2, "8": 1, "6": 1}),
        # Символы вне диапазона цифр просто не считаются.
        ("1a2b3c", {"1": 1, "2": 1, "3": 1}),
    ],
)
def test_counting(content: str, expected: dict[str, int]) -> None:
    counts = DigitCounts.from_content(content)
    assert counts.as_dict() == {digit: expected.get(digit, 0) for digit in DIGITS}


@pytest.mark.parametrize("length", [0, 1, 10, 500])
def test_total_matches_digit_count(length: int) -> None:
    assert DigitCounts.from_content("7" * length).total == length


def test_zero_has_no_counts() -> None:
    assert DigitCounts.zero().total == 0


@pytest.mark.parametrize(
    ("content", "digit", "expected_share"),
    [("1111", "1", 100.0), ("1122", "1", 50.0), ("1234", "9", 0.0)],
)
def test_share(content: str, digit: str, expected_share: float) -> None:
    assert DigitCounts.from_content(content).share(digit) == pytest.approx(expected_share)


def test_share_of_empty_content_is_zero_not_error() -> None:
    assert DigitCounts.zero().share("5") == 0.0
