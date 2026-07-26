"""ASGI-middleware запроса.

Чистый ASGI, а не ``BaseHTTPMiddleware``: последний выполняет обработчик в
отдельной задаче, из-за чего значения contextvar, выставленные внутри, наружу
не возвращаются — а идентификатор запроса нужен именно сквозным.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.logging import request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "x-request-id"
MILLISECONDS = 1000

#: Пути, для которых access-запись не пишется. Проверка живости приходит раз
#: в полминуты, статика — на каждый переход; ни то ни другое не несёт
#: сведений, ради которых стоит листать логи.
DEFAULT_QUIET_PATHS = ("/health", "/static")

SERVER_ERROR = 500
CLIENT_ERROR = 400


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, quiet_paths: Iterable[str] = DEFAULT_QUIET_PATHS) -> None:
        self._app = app
        self._quiet_paths = tuple(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid4().hex
        token = request_id_var.set(request_id)
        started_at = time.perf_counter()
        status_code = SERVER_ERROR

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Возвращаем идентификатор клиенту: по нему пользователь может
                # назвать конкретный запрос, а мы — найти его в логах.
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            self._log(scope, status_code, time.perf_counter() - started_at)
            request_id_var.reset(token)

    def _log(self, scope: Scope, status_code: int, elapsed_seconds: float) -> None:
        path = scope.get("path", "")
        if path.startswith(self._quiet_paths):
            return

        logger.log(
            _level_for(status_code),
            "%s %s -> %s",
            scope.get("method", "?"),
            path,
            status_code,
            extra={
                "event": "http.request",
                "method": scope.get("method"),
                "path": path,
                "status": status_code,
                "duration_ms": round(elapsed_seconds * MILLISECONDS, 1),
            },
        )


def _incoming_request_id(scope: Scope) -> str | None:
    """Подхватить идентификатор, выданный прокси, если он есть.

    Так запрос прослеживается насквозь, а не начинает историю заново на
    каждом узле.
    """
    for name, value in scope.get("headers", ()):
        if name.decode("latin-1").lower() == REQUEST_ID_HEADER:
            return value.decode("latin-1")
    return None


def _level_for(status_code: int) -> int:
    if status_code >= SERVER_ERROR:
        return logging.ERROR
    if status_code >= CLIENT_ERROR:
        return logging.WARNING
    return logging.INFO
