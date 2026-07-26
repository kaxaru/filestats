"""Проверка доступности хранилища."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


class DatabaseHealth:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def is_reachable(self) -> bool:
        """Настоящий запрос, а не проверка объекта подключения.

        Пул отдаёт соединение и когда база уже недоступна, поэтому проверять
        нужно выполнением запроса — иначе healthcheck будет бодро отвечать
        «всё хорошо» на мёртвой базе.
        """
        try:
            async with self._sessions() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("база недоступна")
            return False
        return True
