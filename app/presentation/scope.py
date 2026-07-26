"""Область выбора файлов в интерфейсе."""

from __future__ import annotations

import enum

from app.domain.selection import Selection


class SelectionScope(enum.StrEnum):
    """Что именно выбрал пользователь.

    Явное перечисление вместо флага ``"1"`` в скрытом поле: значение приходит
    из формы, ездит в строке запроса и определяет, будет расчёт по отмеченным
    файлам или по всему каталогу.
    """

    chosen = "chosen"
    everything = "everything"

    @property
    def is_everything(self) -> bool:
        return self is SelectionScope.everything

    def to_selection(self, names: list[str]) -> Selection:
        return Selection.everything() if self.is_everything else Selection.of(names)
