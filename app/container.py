"""Композиционный корень.

Единственное место, где слои встречаются: здесь конкретные реализации
подставляются в порты. Всё остальное приложение знает только о протоколах,
поэтому заменить HTTP-клиент заглушкой или Postgres на что-то другое можно, не
трогая ни домен, ни сценарии.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.application.download_service import CatalogDownloadService, RunPauseObserver
from app.application.launch_service import DownloadLauncher
from app.application.ports import CatalogClient, ThrottleObserver
from app.application.progress_service import ProgressService
from app.application.stats_service import StatisticsService
from app.config import Settings
from app.domain.run import DownloadRun
from app.infrastructure.catalog_api.client import HttpCatalogClient
from app.infrastructure.persistence.engine import SessionFactory
from app.infrastructure.persistence.health import DatabaseHealth
from app.infrastructure.persistence.queries import SqlAlchemyCatalogQueries
from app.infrastructure.persistence.repositories import (
    SqlAlchemyFileRepository,
    SqlAlchemyRunRepository,
)
from app.infrastructure.worker import BackgroundWorker
from app.presentation.formatting import MomentFormatter

DOWNLOAD_JOB_NAME = "catalog-download"

#: Фабрика получает настройки, а не контейнер: клиенту каталога нужны только
#: они, а зависимость от всего контейнера сделала бы подмену в тестах шире, чем
#: требуется, и завела бы ссылку на класс из его же собственного модуля.
CatalogClientFactory = Callable[
    [Settings, ThrottleObserver], AbstractAsyncContextManager[CatalogClient]
]


def utc_now() -> datetime:
    return datetime.now(UTC)


@asynccontextmanager
async def http_catalog_client(
    settings: Settings, observer: ThrottleObserver
) -> AsyncIterator[CatalogClient]:
    """Боевой клиент каталога поверх HTTP."""
    timeout = httpx.Timeout(settings.catalog_api.timeout_seconds)
    async with httpx.AsyncClient(base_url=settings.catalog_api.base_url, timeout=timeout) as http:
        yield HttpCatalogClient(
            http,
            api_settings=settings.catalog_api,
            pacing_settings=settings.pacing,
            observer=observer,
        )


class Container:
    def __init__(
        self,
        settings: Settings,
        session_factory=SessionFactory,
        catalog_client: CatalogClientFactory = http_catalog_client,
    ) -> None:
        self.settings = settings
        self.clock = utc_now
        self._catalog_client = catalog_client

        self.files = SqlAlchemyFileRepository(session_factory)
        self.runs = SqlAlchemyRunRepository(session_factory)
        self.queries = SqlAlchemyCatalogQueries(session_factory)
        self.health = DatabaseHealth(session_factory)

        self.moment = MomentFormatter(settings.web.display_timezone)
        self.worker = BackgroundWorker(self.run_download_cycle, name=DOWNLOAD_JOB_NAME)

    # --- сценарии ------------------------------------------------------------

    @property
    def progress(self) -> ProgressService:
        return ProgressService(self.runs, self.queries, self.clock)

    @property
    def statistics(self) -> StatisticsService:
        return self.statistics_of(self.settings.web.page_size)

    def statistics_of(self, page_size: int) -> StatisticsService:
        """Расчёты с заданным размером страницы.

        Страницам сервиса подходит настроенный размер, а клиенту API нужен свой:
        размер выдачи — часть его запроса, а не настройка сервера.
        """
        return StatisticsService(self.queries, page_size=page_size)

    # --- скачивание ----------------------------------------------------------

    async def begin_download(self) -> DownloadRun:
        """Создать прогон и отдать его фоновой задаче."""
        run = await DownloadLauncher(self.runs, self.clock).begin()
        self.worker.spawn()
        return run

    async def resume_download(self) -> None:
        """Подхватить прогон, прерванный рестартом процесса.

        Полный обход каталога идёт часами — терять его из-за деплоя нельзя.
        """
        if self.worker.is_running:
            return
        if await self.runs.active() is not None:
            self.worker.spawn()

    async def run_download_cycle(self) -> None:
        observer = RunPauseObserver(self.runs, self.clock)
        async with self._catalog_client(self.settings, observer) as client:
            await self._download_service(client).execute()

    def _download_service(self, client: CatalogClient) -> CatalogDownloadService:
        return CatalogDownloadService(
            files=self.files,
            runs=self.runs,
            client=client,
            clock=self.clock,
            confirm_batch_size=self.settings.download.confirm_batch_size,
        )
