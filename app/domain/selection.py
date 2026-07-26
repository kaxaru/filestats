"""Выбор файлов для расчётов."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True)
class Selection:
    """Какие файлы участвуют в расчёте.

    «Вообще все» — это отдельное состояние, а не список из всех имён. Иначе
    выбор всего каталога превращался бы в перечисление тысяч значений в теле
    запроса, хотя на стороне хранилища это просто отсутствие условия.
    """

    names: tuple[str, ...] | None

    @classmethod
    def everything(cls) -> Self:
        return cls(names=None)

    @classmethod
    def of(cls, names: Iterable[str]) -> Self:
        return cls(names=tuple(dict.fromkeys(names)))

    @property
    def is_everything(self) -> bool:
        return self.names is None

    @property
    def is_empty(self) -> bool:
        return self.names is not None and len(self.names) == 0
