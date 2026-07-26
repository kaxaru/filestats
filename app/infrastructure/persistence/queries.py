"""Запросы чтения для интерфейса.

Отдельно от репозиториев: страницам нужны срезы и агрегаты, а не агрегаты
предметной области. Поднимать тысячи объектов в память, чтобы сложить десять
чисел, — это работа для SQL.
"""

from __future__ import annotations

import math

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import ColumnElement

from app.application.ports import FileDetails, FileDigits, FilePage
from app.domain.digits import DIGITS, DigitCounts
from app.domain.file import STATUSES_WITH_CONTENT, CatalogFile
from app.domain.selection import Selection
from app.domain.sorting import SortField, Sorting
from app.domain.values import FileName
from app.infrastructure.persistence.orm import files_table

DIGIT_COLUMNS = [files_table.c[f"d{digit}"] for digit in DIGITS]


# Колонки таблицы, а не атрибуты модели: те же выражения годятся и для выборки
# агрегатов, где никакой модели не участвует.
_SORT_COLUMNS = {
    SortField.downloaded_at: files_table.c.downloaded_at,
    SortField.name: files_table.c.name,
}


def _ordering(sorting: Sorting) -> list[ColumnElement]:
    """Порядок сортировки с устойчивым дополнительным ключом.

    Имя добавляется вторым ключом всегда, кроме случая, когда оно и есть
    основной: без него файлы с одинаковым временем скачивания раскладывались бы
    по страницам произвольно, и один и тот же файл мог бы попасть на две
    страницы сразу или не попасть ни на одну.
    """
    column = _SORT_COLUMNS[sorting.field]
    primary = column.desc() if sorting.direction.is_descending else column.asc()
    if sorting.field is SortField.name:
        return [primary]
    return [primary, files_table.c.name.asc()]


def _only_downloaded(statement: Select) -> Select:
    return statement.where(files_table.c.status.in_(STATUSES_WITH_CONTENT))


def _apply(statement: Select, selection: Selection) -> Select:
    """Наложить выбор пользователя.

    «Вообще все» — это отсутствие условия, а не перечисление всех имён.
    """
    if selection.is_everything:
        return statement
    return statement.where(files_table.c.name.in_(list(selection.names or ())))


class SqlAlchemyCatalogQueries:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def names_discovered(self) -> int:
        return await self._scalar(select(func.count()).select_from(files_table))

    async def files_with_content(self) -> int:
        return await self._scalar(_only_downloaded(select(func.count()).select_from(files_table)))

    async def page(self, *, number: int, size: int, sorting: Sorting) -> FilePage:
        total = await self.files_with_content()
        pages = max(1, math.ceil(total / size))
        number = min(max(number, 1), pages)

        async with self._sessions() as session:
            result = await session.execute(
                _only_downloaded(select(CatalogFile))
                .order_by(*_ordering(sorting))
                .limit(size)
                .offset((number - 1) * size)
            )
            records = tuple(result.scalars())
            session.expunge_all()

        return FilePage(records=records, total=total, number=number, pages=pages)

    async def file_details(self, name: FileName) -> FileDetails | None:
        statement = _only_downloaded(
            select(
                files_table.c.name,
                files_table.c.downloaded_at,
                files_table.c.content,
                *DIGIT_COLUMNS,
            )
        ).where(files_table.c.name == name.value)

        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()

        if row is None:
            return None
        return FileDetails(
            name=row[0],
            downloaded_at=row[1],
            content=row[2],
            counts=DigitCounts(*(int(value) for value in row[3:])),
        )

    async def count_selected(self, selection: Selection) -> int:
        return await self._scalar(
            _apply(_only_downloaded(select(func.count()).select_from(files_table)), selection)
        )

    async def digit_totals(self, selection: Selection) -> DigitCounts:
        statement = _apply(
            _only_downloaded(
                select(*[func.coalesce(func.sum(column), 0) for column in DIGIT_COLUMNS])
            ),
            selection,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).one()
        return DigitCounts(*(int(value) for value in row))

    async def per_file_digits(
        self, selection: Selection, *, sorting: Sorting, limit: int, offset: int = 0
    ) -> list[FileDigits]:
        statement = (
            _apply(
                _only_downloaded(
                    select(files_table.c.name, files_table.c.downloaded_at, *DIGIT_COLUMNS)
                ),
                selection,
            )
            .order_by(*_ordering(sorting))
            .limit(limit)
            .offset(offset)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()

        return [
            FileDigits(
                name=row[0],
                downloaded_at=row[1],
                counts=DigitCounts(*(int(value) for value in row[2:])),
            )
            for row in rows
        ]

    async def _scalar(self, statement: Select) -> int:
        async with self._sessions() as session:
            return (await session.execute(statement)).scalar_one()
