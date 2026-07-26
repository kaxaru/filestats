"""Общий стенд для сквозных тестов.

Поднимается весь стек — приложение, база, маршруты, шаблоны, — подменяется
только клиент каталога. Заглушка не ради скорости: боевой каталог отдаёт каждый
файл ровно один раз на идентификатор, и прогон тестов по нему безвозвратно
расходовал бы данные задания.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from app.config import Settings
from app.container import Container
from app.main import create_app
from tests.fakes import FakeCatalogClient, catalog_of

CATALOG_SIZE = 23
PAGE_SIZE = 10

# Содержимое подобрано так, чтобы ожидаемые счётчики считались в уме:
# каждая цифра встречается ровно 50 раз в каждом файле.
CONTENT = "0123456789" * 50
EXPECTED_PER_DIGIT_PER_FILE = 50


@pytest.fixture
def catalog() -> dict[str, str]:
    return {name: CONTENT for name in catalog_of(CATALOG_SIZE)}


@pytest.fixture
def settings(database_url: str) -> Settings:
    configured = Settings(database_url=database_url)
    configured.web.page_size = PAGE_SIZE
    return configured


@pytest.fixture
async def stack(sessions, settings, catalog):
    """Приложение с подставным каталогом и настоящей базой."""
    client = FakeCatalogClient(catalog)

    @asynccontextmanager
    async def catalog_client(_settings, _observer):
        yield client

    container = Container(settings, session_factory=sessions, catalog_client=catalog_client)
    application = create_app(container)

    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, container, client


async def download_everything(stack) -> None:
    """Прогнать обход до конца — большинству проверок нужен полный каталог."""
    http, container, _ = stack
    response = await http.post("/download/start", follow_redirects=False)
    assert response.status_code == 303
    await container.worker.wait()
