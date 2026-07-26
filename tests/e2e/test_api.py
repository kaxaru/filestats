"""REST API на полном стеке.

Проверяется контракт, который увидит клиент: коды ответов, форма тела,
пагинация, сортировка и поведение на краях. Стенд — общий, из ``conftest.py``.
"""

from __future__ import annotations

import pytest

from app.domain.digits import DIGITS
from app.domain.values import FileName
from tests.e2e.conftest import (
    CATALOG_SIZE,
    EXPECTED_PER_DIGIT_PER_FILE,
    download_everything,
)

API = "/api/v1"


# --- список файлов ------------------------------------------------------------


async def test_files_are_listed_with_pagination(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"size": 10})).json()

    assert body["total"] == CATALOG_SIZE
    assert body["pages"] == 3
    assert body["page"] == 1
    assert len(body["items"]) == 10


async def test_last_page_holds_the_remainder(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"size": 10, "page": 3})).json()
    assert len(body["items"]) == CATALOG_SIZE % 10


async def test_page_beyond_the_last_is_clamped(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"page": 999})).json()
    assert body["page"] == body["pages"]


async def test_file_item_carries_digit_counts(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    item = (await http.get(f"{API}/files", params={"size": 1})).json()["items"][0]

    assert set(item) == {"name", "downloaded_at", "digits"}
    assert item["digits"]["total"] == EXPECTED_PER_DIGIT_PER_FILE * len(DIGITS)
    assert item["digits"]["counts"]["7"] == EXPECTED_PER_DIGIT_PER_FILE


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_sorting_by_name(stack, direction: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (
        await http.get(f"{API}/files", params={"sort": "name", "dir": direction, "size": 100})
    ).json()
    names = [item["name"] for item in body["items"]]

    assert names == sorted(names, reverse=direction == "desc")


async def test_unknown_sort_falls_back_instead_of_failing(stack) -> None:
    """Страница важнее придирки к параметру."""
    http, _, _ = stack
    await download_everything(stack)

    response = await http.get(f"{API}/files", params={"sort": "неизвестно", "dir": "боком"})
    assert response.status_code == 200


@pytest.mark.parametrize(("param", "value"), [("page", 0), ("size", 0), ("size", 10_000)])
async def test_out_of_range_pagination_is_rejected(stack, param: str, value: int) -> None:
    """Границы пагинации — часть контракта, а не пожелание."""
    http, _, _ = stack
    response = await http.get(f"{API}/files", params={param: value})
    assert response.status_code == 422


async def test_empty_catalog_yields_an_empty_page(stack) -> None:
    http, _, _ = stack
    body = (await http.get(f"{API}/files")).json()

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 1


# --- отдельный файл -----------------------------------------------------------


async def test_file_details_include_content(stack, catalog) -> None:
    http, _, _ = stack
    await download_everything(stack)

    name = sorted(catalog)[0]
    body = (await http.get(f"{API}/files/{name}")).json()

    assert body["name"] == name
    assert len(body["content"]) == 500
    assert body["digits"]["counts"]["0"] == EXPECTED_PER_DIGIT_PER_FILE


async def test_unknown_file_is_not_found(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    response = await http.get(f"{API}/files/нет-такого.txt")
    assert response.status_code == 404
    assert "detail" in response.json()


async def test_malformed_name_is_not_found_too(stack) -> None:
    """Для клиента «недопустимое имя» и «нет такого файла» — одно и то же."""
    http, _, _ = stack
    response = await http.get(f"{API}/files/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404


async def test_file_without_content_is_not_found(stack, catalog) -> None:
    """Имя известно, но содержимое не скачано — для API файла ещё нет."""
    http, container, _ = stack
    await container.files.register_discovered([FileName(sorted(catalog)[0])], container.clock())

    response = await http.get(f"{API}/files/{sorted(catalog)[0]}")
    assert response.status_code == 404


# --- расчёты ------------------------------------------------------------------


async def test_stats_over_the_whole_catalog(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats", json={"scope": "everything"})).json()

    assert body["files_selected"] == CATALOG_SIZE
    assert body["totals"]["total"] == EXPECTED_PER_DIGIT_PER_FILE * len(DIGITS) * CATALOG_SIZE
    for digit in DIGITS:
        assert body["totals"]["counts"][digit] == EXPECTED_PER_DIGIT_PER_FILE * CATALOG_SIZE


async def test_stats_over_chosen_files(stack, catalog) -> None:
    http, _, _ = stack
    await download_everything(stack)

    chosen = sorted(catalog)[:3]
    body = (await http.post(f"{API}/stats", json={"scope": "chosen", "names": chosen})).json()

    assert body["files_selected"] == 3
    assert body["totals"]["total"] == EXPECTED_PER_DIGIT_PER_FILE * len(DIGITS) * 3


async def test_totals_cover_the_selection_even_when_breakdown_is_paginated(stack) -> None:
    """Общий итог — по всей выборке, разбивка — постранично."""
    http, _, _ = stack
    await download_everything(stack)

    body = (
        await http.post(f"{API}/stats", json={"scope": "everything"}, params={"size": 5})
    ).json()

    assert len(body["per_file"]) == 5
    assert body["pages"] == 5
    assert body["files_selected"] == CATALOG_SIZE
    assert body["totals"]["total"] == EXPECTED_PER_DIGIT_PER_FILE * len(DIGITS) * CATALOG_SIZE


async def test_stats_breakdown_can_be_sorted(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (
        await http.post(
            f"{API}/stats",
            json={"scope": "everything"},
            params={"sort": "name", "dir": "desc", "size": 100},
        )
    ).json()
    names = [item["name"] for item in body["per_file"]]

    assert names == sorted(names, reverse=True)


async def test_empty_selection_yields_zeros(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats", json={"scope": "chosen", "names": []})).json()

    assert body["files_selected"] == 0
    assert body["totals"]["total"] == 0
    assert body["per_file"] == []


async def test_stats_without_body_defaults_to_chosen_nothing(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats")).json()
    assert body["files_selected"] == 0


async def test_unknown_scope_is_rejected(stack) -> None:
    http, _, _ = stack
    response = await http.post(f"{API}/stats", json={"scope": "всё подряд"})
    assert response.status_code == 422


# --- обход каталога -----------------------------------------------------------


async def test_latest_run_before_anything_happened(stack) -> None:
    http, _, _ = stack
    body = (await http.get(f"{API}/runs/latest")).json()

    assert body["run"] is None
    assert body["names_discovered"] == 0
    assert body["percent"] == 0
    assert body["is_running"] is False


async def test_run_can_be_started_and_reports_progress(stack, catalog) -> None:
    http, container, client = stack

    started = await http.post(f"{API}/runs")
    assert started.status_code == 202
    assert started.json()["status"] == "running"

    await container.worker.wait()

    body = (await http.get(f"{API}/runs/latest")).json()
    assert body["run"]["status"] == "completed"
    assert body["files_downloaded"] == CATALOG_SIZE
    assert body["percent"] == 100
    assert client.confirmed == set(catalog)


async def test_starting_twice_returns_the_same_run(stack) -> None:
    """Идемпотентность не удобство: два обхода с одного адреса ускорили бы бан."""
    http, container, _ = stack

    first = (await http.post(f"{API}/runs")).json()
    second = (await http.post(f"{API}/runs")).json()
    await container.worker.wait()

    assert first["id"] == second["id"]


async def test_run_reports_pacing_measurements(stack) -> None:
    http, container, client = stack

    await http.post(f"{API}/runs")
    await container.worker.wait()

    pacing = (await http.get(f"{API}/runs/latest")).json()["run"]["pacing"]
    assert pacing["requests_made"] == len(client.calls)
    assert pacing["throttle_events"] == 0
    assert pacing["requests_per_minute"] is not None


# --- документация -------------------------------------------------------------


async def test_openapi_describes_only_the_api(stack) -> None:
    """Swagger документирует API, а не HTML-страницы."""
    http, _, _ = stack
    schema = (await http.get("/openapi.json")).json()

    paths = set(schema["paths"])
    assert all(path.startswith("/api/v1") for path in paths), paths
    assert f"{API}/files/{{name}}" in paths


async def test_docs_are_served(stack) -> None:
    http, _, _ = stack
    assert (await http.get("/docs")).status_code == 200
