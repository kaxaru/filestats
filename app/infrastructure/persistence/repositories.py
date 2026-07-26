"""Репозитории поверх SQLAlchemy.

Каждый метод — короткая самостоятельная транзакция. Прогон скачивания идёт
часами, и держать открытую транзакцию всё это время нельзя: она копила бы
блокировки и мешала бы читающим страницам. Поэтому объекты после чтения
отвязываются от сессии, а запись выполняется через ``merge``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.file import STATUSES_WITH_CONTENT, CatalogFile, FileStatus
from app.domain.run import DownloadRun, RunStatus
from app.domain.values import FileName
from app.infrastructure.persistence.orm import files_table


class SqlAlchemyFileRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def register_discovered(self, names: list[FileName], now: datetime) -> None:
        if not names:
            return
        async with self._sessions() as session:
            statement = pg_insert(files_table).values(
                [
                    {
                        "name": name.value,
                        "status": FileStatus.discovered,
                        "discovered_at": now,
                    }
                    for name in names
                ]
            )
            # Уже известные имена не трогаем: повторная выдача одного и того же
            # имени — штатное поведение каталога, а не повод обнулять прогресс.
            await session.execute(statement.on_conflict_do_nothing(index_elements=["name"]))
            await session.commit()

    async def names_without_content(self, names: list[FileName]) -> list[FileName]:
        if not names:
            return []
        async with self._sessions() as session:
            result = await session.execute(
                select(files_table.c.name).where(
                    files_table.c.name.in_([name.value for name in names]),
                    files_table.c.status.in_(STATUSES_WITH_CONTENT),
                )
            )
            # Колонка объявлена как FileNameType, поэтому из выборки приходят
            # уже готовые объекты-значения — оборачивать их повторно не нужно.
            known = set(result.scalars())
        return [name for name in names if name not in known]

    async def get_many(self, names: list[FileName]) -> list[CatalogFile]:
        if not names:
            return []
        async with self._sessions() as session:
            result = await session.execute(
                select(CatalogFile).where(CatalogFile.name.in_([name.value for name in names]))
            )
            files = list(result.scalars())
            session.expunge_all()
        return files

    async def save_many(self, files: list[CatalogFile]) -> None:
        if not files:
            return
        async with self._sessions() as session:
            for file in files:
                await session.merge(file)
            await session.commit()

    async def awaiting_confirmation(self, limit: int) -> list[CatalogFile]:
        async with self._sessions() as session:
            result = await session.execute(
                select(CatalogFile)
                .where(CatalogFile.status == FileStatus.downloaded)
                .order_by(CatalogFile.downloaded_at.asc())
                .limit(limit)
            )
            files = list(result.scalars())
            session.expunge_all()
        return files

    async def count_awaiting_confirmation(self) -> int:
        return await self._count(files_table.c.status == FileStatus.downloaded)

    async def count_with_content(self) -> int:
        return await self._count(files_table.c.status.in_(STATUSES_WITH_CONTENT))

    async def _count(self, condition) -> int:
        async with self._sessions() as session:
            result = await session.execute(
                select(func.count()).select_from(files_table).where(condition)
            )
            return result.scalar_one()


class SqlAlchemyRunRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def active(self) -> DownloadRun | None:
        return await self._first(RunStatus.running)

    async def latest(self) -> DownloadRun | None:
        return await self._first(None)

    async def add(self, run: DownloadRun) -> DownloadRun:
        async with self._sessions() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
            session.expunge_all()
        return run

    async def save(self, run: DownloadRun) -> None:
        async with self._sessions() as session:
            await session.merge(run)
            await session.commit()

    async def _first(self, status: RunStatus | None) -> DownloadRun | None:
        statement = select(DownloadRun).order_by(DownloadRun.id.desc()).limit(1)
        if status is not None:
            statement = statement.where(DownloadRun.status == status)
        async with self._sessions() as session:
            run = (await session.execute(statement)).scalar_one_or_none()
            session.expunge_all()
        return run
