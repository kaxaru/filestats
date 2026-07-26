"""Общие фикстуры.

База поднимается настоящая: репозитории и запросы чтения написаны на SQL и
проверяются против того же Postgres, что и в бою. Подменять его на SQLite
означало бы проверять не то, что поедет в прод.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.persistence.orm import metadata

#: Порт намеренно не 5432: на машине разработчика там обычно уже стоит свой
#: Postgres, а тестовая база поднимается отдельным одноразовым контейнером.
#: Команда для него — в README, раздел «Локальный запуск».
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://filestats:filestats@localhost:5433/filestats_test"


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
async def sessions(database_url: str):
    """Чистая схема на каждый тест."""
    # Пул по умолчанию — намеренно: с NullPool каждый запрос открывал бы новое
    # соединение, и прогон замедлялся почти в десять раз. Движок живёт один
    # тест и закрывается в конце, копить соединения ему негде.
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()
