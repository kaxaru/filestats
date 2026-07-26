"""HTTP-клиент каталога: лимиты, баны, неполные пачки.

Реальные паузы не выдерживаются — сон подменён, а запрошенные длительности
записываются. Проверяется логика ожидания, а не терпение.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from http import HTTPStatus

import httpx
import pytest
import respx

from app.application.errors import (
    CatalogThrottled,
    CatalogUnavailable,
    FileMissingFromCatalog,
)
from app.config import CatalogApiSettings, PacingSettings
from app.domain.pausing import PauseReason
from app.domain.values import BAN_DURATION_SECONDS, MAX_FILES_PER_DOWNLOAD, FileName
from app.infrastructure.catalog_api.client import (
    CANDIDATE_HEADER,
    CONFIRM_PATH,
    DOWNLOAD_PATH,
    NAMES_PATH,
    HttpCatalogClient,
)

BASE = "http://catalog.test"
CANDIDATE = "test-candidate"


@pytest.fixture
def api() -> CatalogApiSettings:
    return CatalogApiSettings(base_url=BASE, candidate_id=CANDIDATE, network_retries=2)


@pytest.fixture
def pacing() -> PacingSettings:
    return PacingSettings(
        initial_interval_seconds=0.01,
        min_interval_seconds=0.01,
        retry_after_padding_seconds=0.0,
        max_consecutive_throttles=3,
    )


class RecordingObserver:
    def __init__(self) -> None:
        self.pauses = []

    async def on_wait(self, pause) -> None:
        self.pauses.append(pause)


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args, **kwargs):
        recorded.append(delay)
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class ClientHarness:
    def __init__(self, api, pacing, observer=None):
        self.http = httpx.AsyncClient(base_url=BASE)
        self.client = HttpCatalogClient(
            self.http, api_settings=api, pacing_settings=pacing, observer=observer
        )

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc_info):
        await self.http.aclose()


# --- нормальная работа --------------------------------------------------------


@respx.mock
async def test_names_are_parsed_into_value_objects(api, pacing) -> None:
    respx.get(f"{BASE}{NAMES_PATH}").mock(
        return_value=httpx.Response(200, json={"file_names": ["a.txt", "b.txt"]})
    )
    async with ClientHarness(api, pacing) as client:
        names = await client.fetch_names()

    assert names == [FileName("a.txt"), FileName("b.txt")]


@respx.mock
async def test_empty_names_mean_catalog_is_done(api, pacing) -> None:
    respx.get(f"{BASE}{NAMES_PATH}").mock(return_value=httpx.Response(200, json={"file_names": []}))
    async with ClientHarness(api, pacing) as client:
        assert await client.fetch_names() == []


@respx.mock
@pytest.mark.parametrize("path", [NAMES_PATH])
async def test_candidate_header_is_always_sent(api, pacing, path: str) -> None:
    route = respx.get(f"{BASE}{path}").mock(
        return_value=httpx.Response(200, json={"file_names": []})
    )
    async with ClientHarness(api, pacing) as client:
        await client.fetch_names()

    assert route.calls[0].request.headers[CANDIDATE_HEADER] == CANDIDATE


@respx.mock
async def test_download_unpacks_archive(api, pacing) -> None:
    respx.post(f"{BASE}{DOWNLOAD_PATH}").mock(
        return_value=httpx.Response(200, content=make_zip({"a.txt": "123", "b.txt": "456"}))
    )
    async with ClientHarness(api, pacing) as client:
        result = await client.fetch_contents([FileName("a.txt"), FileName("b.txt")])

    assert {str(name): content.raw for name, content in result.items()} == {
        "a.txt": "123",
        "b.txt": "456",
    }


@respx.mock
async def test_archive_entries_with_paths_are_flattened(api, pacing) -> None:
    respx.post(f"{BASE}{DOWNLOAD_PATH}").mock(
        return_value=httpx.Response(200, content=make_zip({"nested/dir/a.txt": "111"}))
    )
    async with ClientHarness(api, pacing) as client:
        result = await client.fetch_contents([FileName("a.txt")])

    assert list(result) == [FileName("a.txt")]


async def test_download_refuses_oversized_batch(api, pacing) -> None:
    names = [FileName(f"{index}.txt") for index in range(MAX_FILES_PER_DOWNLOAD + 1)]
    async with ClientHarness(api, pacing) as client:
        with pytest.raises(ValueError, match=str(MAX_FILES_PER_DOWNLOAD)):
            await client.fetch_contents(names)


@respx.mock
async def test_confirmation_reports_counts(api, pacing) -> None:
    respx.post(f"{BASE}{CONFIRM_PATH}").mock(
        return_value=httpx.Response(200, json={"marked_now": 2, "already_marked": 1})
    )
    async with ClientHarness(api, pacing) as client:
        outcome = await client.confirm_downloaded([FileName("a.txt")])

    assert (outcome.marked_now, outcome.already_marked) == (2, 1)


@respx.mock
async def test_confirmation_accepts_batches_beyond_download_limit(api, pacing) -> None:
    """У отметки нет ограничения на количество имён — этим и пользуемся."""
    route = respx.post(f"{BASE}{CONFIRM_PATH}").mock(
        return_value=httpx.Response(200, json={"marked_now": 30, "already_marked": 0})
    )
    names = [FileName(f"file-{index}.txt") for index in range(30)]

    async with ClientHarness(api, pacing) as client:
        await client.confirm_downloaded(names)

    sent = json.loads(route.calls[0].request.content)["file_names"]
    assert len(sent) == 30


@respx.mock
async def test_empty_confirmation_does_not_hit_the_network(api, pacing) -> None:
    route = respx.post(f"{BASE}{CONFIRM_PATH}")
    async with ClientHarness(api, pacing) as client:
        outcome = await client.confirm_downloaded([])

    assert outcome.marked_now == 0
    assert route.call_count == 0


# --- лимиты и баны ------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    ("status", "retry_after", "reason"),
    [
        (HTTPStatus.TOO_MANY_REQUESTS, "12", PauseReason.rate_limited),
        (HTTPStatus.TOO_MANY_REQUESTS, "1", PauseReason.rate_limited),
        (HTTPStatus.FORBIDDEN, str(BAN_DURATION_SECONDS), PauseReason.banned),
    ],
)
async def test_refusal_is_waited_out_then_retried(
    api, pacing, slept, status, retry_after, reason
) -> None:
    route = respx.get(f"{BASE}{NAMES_PATH}").mock(
        side_effect=[
            httpx.Response(status, headers={"Retry-After": retry_after}, json={"detail": "нет"}),
            httpx.Response(200, json={"file_names": ["a.txt"]}),
        ]
    )
    observer = RecordingObserver()
    async with ClientHarness(api, pacing, observer) as client:
        assert await client.fetch_names() == [FileName("a.txt")]

    assert route.call_count == 2
    assert observer.pauses[0].reason is reason
    assert observer.pauses[0].seconds == pytest.approx(float(retry_after))
    # Ожидание не короче назначенного сервером.
    assert max(slept) >= float(retry_after) * 0.99


@respx.mock
async def test_missing_retry_after_uses_documented_ban_duration(api, pacing, slept) -> None:
    respx.get(f"{BASE}{NAMES_PATH}").mock(
        side_effect=[
            httpx.Response(HTTPStatus.FORBIDDEN, json={"detail": "бан"}),
            httpx.Response(200, json={"file_names": []}),
        ]
    )
    observer = RecordingObserver()
    async with ClientHarness(api, pacing, observer) as client:
        await client.fetch_names()

    assert observer.pauses[0].seconds == pytest.approx(float(BAN_DURATION_SECONDS))


@respx.mock
async def test_endless_refusals_give_up_instead_of_hanging(api, pacing, slept) -> None:
    """Молча ждать сутками хуже, чем остановиться с внятной причиной."""
    respx.get(f"{BASE}{NAMES_PATH}").mock(
        return_value=httpx.Response(
            HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "5"}, json={"detail": "нет"}
        )
    )
    async with ClientHarness(api, pacing) as client:
        with pytest.raises(CatalogThrottled):
            await client.fetch_names()


@respx.mock
async def test_success_resets_the_refusal_streak(api, pacing, slept) -> None:
    limited = httpx.Response(
        HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "1"}, json={"detail": "нет"}
    )
    ok = httpx.Response(200, json={"file_names": []})
    # Отказов больше, чем предел, но они разделены успехом.
    respx.get(f"{BASE}{NAMES_PATH}").mock(side_effect=[limited, limited, ok, limited, limited, ok])
    async with ClientHarness(api, pacing) as client:
        await client.fetch_names()
        await client.fetch_names()


# --- отсутствующие файлы ------------------------------------------------------


@respx.mock
async def test_partial_batch_is_retried_one_by_one(api, pacing) -> None:
    """Одно битое имя не должно уносить с собой исправные файлы."""

    def handler(request: httpx.Request) -> httpx.Response:
        names = json.loads(request.content)["file_names"]
        if len(names) > 1:
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"detail": "часть отсутствует"})
        if names == ["missing.txt"]:
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"detail": "нет такого"})
        return httpx.Response(200, content=make_zip({names[0]: "999"}))

    respx.post(f"{BASE}{DOWNLOAD_PATH}").mock(side_effect=handler)

    async with ClientHarness(api, pacing) as client:
        result = await client.fetch_contents(
            [FileName("good1.txt"), FileName("missing.txt"), FileName("good2.txt")]
        )

    assert {str(name) for name in result} == {"good1.txt", "good2.txt"}


@respx.mock
async def test_single_missing_file_surfaces(api, pacing) -> None:
    respx.post(f"{BASE}{DOWNLOAD_PATH}").mock(
        return_value=httpx.Response(HTTPStatus.NOT_FOUND, json={"detail": "нет"})
    )
    async with ClientHarness(api, pacing) as client:
        with pytest.raises(FileMissingFromCatalog):
            await client.fetch_contents([FileName("missing.txt")])


# --- сеть ---------------------------------------------------------------------


@respx.mock
async def test_network_failures_are_retried_then_reported(api, pacing, slept) -> None:
    respx.get(f"{BASE}{NAMES_PATH}").mock(side_effect=httpx.ConnectError("нет связи"))
    observer = RecordingObserver()

    async with ClientHarness(api, pacing, observer) as client:
        with pytest.raises(CatalogUnavailable):
            await client.fetch_names()

    assert len(observer.pauses) == api.network_retries
    assert all(pause.reason is PauseReason.network_error for pause in observer.pauses)


@respx.mock
async def test_network_failure_recovers_before_limit(api, pacing, slept) -> None:
    respx.get(f"{BASE}{NAMES_PATH}").mock(
        side_effect=[httpx.ConnectError("сбой"), httpx.Response(200, json={"file_names": []})]
    )
    async with ClientHarness(api, pacing) as client:
        assert await client.fetch_names() == []
