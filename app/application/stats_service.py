"""Сценарий расчётов по содержимому файлов."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.application.ports import CatalogQueries, FileDigits
from app.domain.digits import DigitCounts
from app.domain.selection import Selection
from app.domain.sorting import Sorting


@dataclass(frozen=True, slots=True)
class CatalogStatistics:
    """Результат расчёта.

    Общий итог считается по всей выборке целиком — одним агрегатом на стороне
    базы, независимо от того, сколько файлов выбрано. Разбивка по файлам
    выдаётся постранично: тысячи строк не нужны ни экрану, ни ответу, но и
    прятать их за «показаны первые N» незачем — постраничный обход даёт доступ
    ко всем, как и в списке файлов.
    """

    totals: DigitCounts
    per_file: tuple[FileDigits, ...]
    files_selected: int
    page: int
    pages: int
    page_size: int
    sorting: Sorting

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def first_row_number(self) -> int:
        """Номер первой строки страницы — чтобы нумерация шла сквозной.

        Считается от размера страницы, а не от длины выдачи: последняя
        страница короче, и от неё смещение получилось бы неверным.
        """
        return (self.page - 1) * self.page_size + 1


class StatisticsService:
    def __init__(self, queries: CatalogQueries, *, page_size: int) -> None:
        self._queries = queries
        self._page_size = page_size

    async def compute(
        self,
        selection: Selection,
        *,
        page: int = 1,
        sorting: Sorting | None = None,
    ) -> CatalogStatistics:
        order = sorting or Sorting()
        files_selected = await self._queries.count_selected(selection)
        pages = max(1, math.ceil(files_selected / self._page_size))
        page = min(max(page, 1), pages)

        return CatalogStatistics(
            totals=await self._queries.digit_totals(selection),
            per_file=tuple(
                await self._queries.per_file_digits(
                    selection,
                    sorting=order,
                    limit=self._page_size,
                    offset=(page - 1) * self._page_size,
                )
            ),
            files_selected=files_selected,
            page=page,
            pages=pages,
            page_size=self._page_size,
            sorting=order,
        )
