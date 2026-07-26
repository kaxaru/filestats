"""Middleware запроса: идентификатор и access-запись."""

from __future__ import annotations

import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.infrastructure.logging import request_id_var
from app.presentation.middleware import REQUEST_ID_HEADER, RequestContextMiddleware

SEEN_INSIDE_HANDLER = "seen"


async def ok(request):
    # Идентификатор должен быть виден обработчику — ради сквозной связности
    # он и заводится.
    return PlainTextResponse(request_id_var.get(), headers={SEEN_INSIDE_HANDLER: "1"})


async def boom(request):
    raise RuntimeError("сломалось")


async def not_found(request):
    return PlainTextResponse("нет", status_code=404)


def build_app() -> Starlette:
    application = Starlette(
        routes=[
            Route("/ok", ok),
            Route("/boom", boom),
            Route("/missing", not_found),
            Route("/health", ok),
            Route("/static/app.css", ok),
        ]
    )
    application.add_middleware(RequestContextMiddleware)
    return application


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_request_id_is_generated_and_returned(client) -> None:
    response = await client.get("/ok")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]
    # Тот же идентификатор виден внутри обработчика.
    assert response.text == response.headers[REQUEST_ID_HEADER]


async def test_incoming_request_id_is_preserved(client) -> None:
    """Идентификатор от прокси подхватывается, а не заводится заново."""
    response = await client.get("/ok", headers={REQUEST_ID_HEADER: "from-proxy-42"})

    assert response.headers[REQUEST_ID_HEADER] == "from-proxy-42"
    assert response.text == "from-proxy-42"


async def test_each_request_gets_its_own_id(client) -> None:
    first = await client.get("/ok")
    second = await client.get("/ok")
    assert first.text != second.text


async def test_context_does_not_leak_between_requests(client) -> None:
    await client.get("/ok", headers={REQUEST_ID_HEADER: "first"})
    assert request_id_var.get() == "-"


async def test_access_line_carries_structured_fields(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.get("/ok")

    entry = next(record for record in caplog.records if record.name == "app.access")
    assert entry.event == "http.request"
    assert entry.method == "GET"
    assert entry.path == "/ok"
    assert entry.status == 200
    assert entry.duration_ms >= 0


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_level"),
    [
        ("/ok", 200, logging.INFO),
        ("/missing", 404, logging.WARNING),
        ("/boom", 500, logging.ERROR),
    ],
)
async def test_log_level_follows_status(
    client, caplog, path: str, expected_status: int, expected_level: int
) -> None:
    """Ошибку не должно приходиться выискивать среди успешных запросов."""
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = await client.get(path)

    assert response.status_code == expected_status
    entry = next(record for record in caplog.records if record.name == "app.access")
    assert entry.levelno == expected_level
    assert entry.status == expected_status


@pytest.mark.parametrize("path", ["/health", "/static/app.css"])
async def test_noisy_paths_are_not_logged(client, caplog, path: str) -> None:
    """Проверка живости раз в полминуты не должна забивать логи."""
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.get(path)

    assert [record for record in caplog.records if record.name == "app.access"] == []


async def test_failed_request_still_gets_an_access_line(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        await client.get("/boom")

    assert any(record.name == "app.access" for record in caplog.records)
