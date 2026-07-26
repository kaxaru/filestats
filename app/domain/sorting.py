"""Сортировка списка файлов."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Self


class SortField(enum.StrEnum):
    """По какому столбцу сортируем."""

    downloaded_at = "downloaded_at"
    name = "name"

    @property
    def default_direction(self) -> SortDirection:
        """Направление при первом нажатии на заголовок.

        Для времени естественно начинать с новых, для имени — с начала
        алфавита. Одинаковое умолчание для обоих раздражало бы в одном из них.
        """
        return (
            SortDirection.descending if self is SortField.downloaded_at else SortDirection.ascending
        )

    def label(self, direction: SortDirection) -> str:
        if self is SortField.downloaded_at:
            return "сначала новые" if direction.is_descending else "сначала старые"
        return "от Я к А" if direction.is_descending else "от А к Я"


class SortDirection(enum.StrEnum):
    ascending = "asc"
    descending = "desc"

    @property
    def is_descending(self) -> bool:
        return self is SortDirection.descending

    @property
    def flipped(self) -> SortDirection:
        return SortDirection.ascending if self.is_descending else SortDirection.descending

    @property
    def arrow(self) -> str:
        return "↓" if self.is_descending else "↑"

    @property
    def aria_value(self) -> str:
        return "descending" if self.is_descending else "ascending"


@dataclass(frozen=True, slots=True)
class Sorting:
    """Столбец и направление вместе.

    Одним объектом, а не двумя параметрами: они осмысленны только в паре, и
    правило «нажали на активный столбец — переверни, на другой — начни с его
    умолчания» живёт здесь, а не размазано по слою представления.
    """

    field: SortField = SortField.downloaded_at
    direction: SortDirection = SortDirection.descending

    @classmethod
    def parse(cls, field: str | None, direction: str | None) -> Self:
        """Разобрать значения из запроса, не падая на мусоре.

        Непонятный параметр сортировки — не повод отдавать ошибку вместо
        страницы: возвращаем умолчание.
        """
        try:
            parsed_field = SortField(field) if field else SortField.downloaded_at
        except ValueError:
            parsed_field = SortField.downloaded_at

        try:
            parsed_direction = (
                SortDirection(direction) if direction else parsed_field.default_direction
            )
        except ValueError:
            parsed_direction = parsed_field.default_direction

        return cls(field=parsed_field, direction=parsed_direction)

    def is_active(self, field: SortField) -> bool:
        return self.field is field

    def toggled(self, field: SortField) -> Sorting:
        """Что произойдёт при нажатии на заголовок столбца."""
        if self.is_active(field):
            return Sorting(field=field, direction=self.direction.flipped)
        return Sorting(field=field, direction=field.default_direction)

    def arrow(self, field: SortField) -> str:
        """Стрелка рисуется только у столбца, по которому идёт сортировка."""
        return self.direction.arrow if self.is_active(field) else ""

    def aria_value(self, field: SortField) -> str:
        return self.direction.aria_value if self.is_active(field) else "none"

    def hint(self, field: SortField) -> str:
        """Подсказка описывает результат нажатия, а не текущее состояние."""
        target = self.toggled(field)
        return f"Сортировать: {target.field.label(target.direction)}"
