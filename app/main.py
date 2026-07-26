"""Точка входа приложения."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.container import Container
from app.infrastructure import logging as app_logging
from app.presentation.middleware import RequestContextMiddleware
from app.presentation.routes import router

STATIC_DIR = Path(__file__).parent / "presentation" / "static"

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

    application = FastAPI(title="Скачивание и анализ файлов", lifespan=lifespan)
    application.add_middleware(RequestContextMiddleware)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(router)
    return application


app = create_app()
