"""Счётчики цифр — объект-значение."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Self

DIGITS: tuple[str, ...] = tuple(str(digit) for digit in range(10))


@dataclass(frozen=True, slots=True)
class DigitCounts:
    """Сколько раз каждая цифра встретилась в содержимом файла.

    Неизменяемое значение, вычисляемое из содержимого. Хранится рядом с файлом,
    чтобы расчёты сводились к сложению чисел, а не к повторному разбору строк:
    содержимое не меняется, поэтому пересчитывать его на каждый запрос незачем.

    Десять полей вместо ``Counter`` — не многословие ради многословия. Класс
    отображается на десять колонок ``d0..d9`` через ``composite()``, а колонки
    нужны, чтобы общая статистика считалась одним ``SUM`` на стороне базы
    независимо от размера выборки. ``Counter`` пришлось бы класть в JSON, и
    агрегат превратился бы в разбор JSON на каждую цифру. Подробнее и с
    условием пересмотра — ``docs/DECISIONS.md``.

    Сложения счётчиков между собой здесь намеренно нет: суммирование живёт в
    SQL, и питонья реализация была бы приглашением обойти это решение.
    """

    d0: int = 0
    d1: int = 0
    d2: int = 0
    d3: int = 0
    d4: int = 0
    d5: int = 0
    d6: int = 0
    d7: int = 0
    d8: int = 0
    d9: int = 0

    @classmethod
    def from_content(cls, content: str) -> Self:
        counts = dict.fromkeys(DIGITS, 0)
        for char in content:
            if char in counts:
                counts[char] += 1
        return cls(**{f"d{digit}": counts[digit] for digit in DIGITS})

    @classmethod
    def zero(cls) -> Self:
        return cls()

    def __getitem__(self, digit: str) -> int:
        return getattr(self, f"d{digit}")

    @property
    def total(self) -> int:
        return sum(getattr(self, f.name) for f in fields(self))

    def as_dict(self) -> dict[str, int]:
        return {digit: self[digit] for digit in DIGITS}

    def share(self, digit: str) -> float:
        """Доля цифры среди всех подсчитанных символов, в процентах."""
        total = self.total
        return self[digit] / total * 100 if total else 0.0
