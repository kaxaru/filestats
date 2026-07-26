"""Точка входа приложения."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.container import Container
from app.infrastructure import logging as app_logging
from app.presentation.api.routes import router as api_router
from app.presentation.middleware import RequestContextMiddleware
from app.presentation.routes import router as pages_router

STATIC_DIR = Path(__file__).parent / "presentation" / "static"

API_DESCRIPTION = """
Сервис скачивает каталог текстовых файлов через внешнее API с ограниченной
частотой запросов и считает статистику по цифрам в их содержимом.

Тот же самый набор данных доступен и в веб-интерфейсе на `/` — API и страницы
работают поверх одних и тех же сценариев, а не двух наборов правил.

**Обход каталога** запускается через `POST /api/v1/runs` и идёт фоном.
Операция идемпотентна: повторный вызов вернёт уже идущий прогон. Следить за
ходом — `GET /api/v1/runs/latest`.
"""

TAGS = [
    {"name": "Файлы", "description": "Список скачанных файлов и содержимое отдельного файла."},
    {"name": "Расчёты", "description": "Статистика по цифрам: общая и в разбивке по файлам."},
    {"name": "Скачивание", "description": "Запуск обхода каталога и наблюдение за ним."},
]

app_logging.configure(level=settings.logging.level, output_format=settings.logging.format)


def create_app(container: Container | None = None) -> FastAPI:
    """Собрать приложение.

    Контейнер можно передать снаружи — на этом держатся e2e-тесты: тот же самый
    стек поднимается против заглушки каталога, а не против боевого API.
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.container = container or Container(settings)

        # Прогон, прерванный рестартом контейнера, продолжается сам: полный
        # обход каталога идёт часами, и деплой не должен его обнулять.
        await application.state.container.resume_download()
        yield
        await application.state.container.worker.stop()

    application = FastAPI(
        title="Сервис скачивания и анализа файлов",
        description=API_DESCRIPTION,
        version="1.0.0",
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Страницы намеренно не попадают в схему: Swagger описывает API, а не
    # HTML-разметку, и смешивать их в одном оглавлении бессмысленно.
    application.include_router(pages_router, include_in_schema=False)
    application.include_router(api_router)
    return application


app = create_app()
