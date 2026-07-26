"""REST API на полном стеке.

Ожидаемые значения выписаны литералами и выводятся из спецификации стенда
(см. ``conftest.py``) словами, а не считаются выражением в момент проверки:
подсчёт по той же формуле, что и в коде, проверял бы формулу саму по себе и
прошёл бы, даже когда обе стороны неверны.

Циклов с проверками внутри здесь нет. Там, где значений несколько, каждое —
отдельный случай ``parametrize``: падение на цифре «0» не должно скрывать
остальные девять.
"""

from __future__ import annotations

import pytest

from app.domain.values import FileName
from tests.e2e.conftest import download_everything

API = "/api/v1"

# Литералы из спецификации стенда: 23 файла по 500 символов, в каждом каждая
# цифра встречается 50 раз, имена — file-0000.txt … file-0022.txt.
FILES_IN_CATALOG = 23
DIGITS_PER_FILE = 500
EACH_DIGIT_PER_FILE = 50
EACH_DIGIT_IN_CATALOG = 1150
DIGITS_IN_CATALOG = 11500
FIRST_NAME = "file-0000.txt"
LAST_NAME = "file-0022.txt"

ALL_DIGITS = [str(value) for value in range(10)]


# --- список файлов ------------------------------------------------------------


async def test_files_are_listed_with_pagination(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"size": 10})).json()

    assert body["total"] == FILES_IN_CATALOG
    assert body["pages"] == 3
    assert body["page"] == 1
    assert len(body["items"]) == 10


async def test_last_page_holds_the_remainder(stack) -> None:
    """23 файла по 10 на страницу — на третьей остаётся три."""
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"size": 10, "page": 3})).json()
    assert len(body["items"]) == 3


async def test_page_beyond_the_last_is_clamped(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"page": 999})).json()
    assert body["page"] == 1
    assert body["pages"] == 1


async def test_file_item_has_the_expected_shape(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    item = (await http.get(f"{API}/files", params={"size": 1})).json()["items"][0]
    assert set(item) == {"name", "downloaded_at", "digits"}


async def test_file_item_reports_total_digits(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    item = (await http.get(f"{API}/files", params={"size": 1})).json()["items"][0]
    assert item["digits"]["total"] == DIGITS_PER_FILE


@pytest.mark.parametrize("digit", ALL_DIGITS)
async def test_file_item_counts_each_digit(stack, digit: str) -> None:
    """Каждая цифра — отдельный случай: расхождение по одной не скроет прочие."""
    http, _, _ = stack
    await download_everything(stack)

    item = (await http.get(f"{API}/files", params={"size": 1})).json()["items"][0]
    assert item["digits"]["counts"][digit] == EACH_DIGIT_PER_FILE


@pytest.mark.parametrize(
    ("direction", "expected_first"),
    [("asc", FIRST_NAME), ("desc", LAST_NAME)],
)
async def test_sorting_by_name_puts_the_right_file_first(
    stack, direction: str, expected_first: str
) -> None:
    """Проверяется конкретное имя, а не отсортированность списка относительно себя.

    ``names == sorted(names)`` прошло бы и на одном файле, и на пустой выдаче.
    """
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files", params={"sort": "name", "dir": direction})).json()
    assert body["items"][0]["name"] == expected_first


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


async def test_file_details_return_the_requested_file(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files/{FIRST_NAME}")).json()
    assert body["name"] == FIRST_NAME


async def test_file_details_include_content_of_expected_length(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files/{FIRST_NAME}")).json()
    assert len(body["content"]) == DIGITS_PER_FILE


@pytest.mark.parametrize("digit", ALL_DIGITS)
async def test_file_details_count_each_digit(stack, digit: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"{API}/files/{FIRST_NAME}")).json()
    assert body["digits"]["counts"][digit] == EACH_DIGIT_PER_FILE


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


async def test_file_without_content_is_not_found(stack) -> None:
    """Имя известно, но содержимое не скачано — для API файла ещё нет."""
    http, container, _ = stack
    await container.files.register_discovered([FileName(FIRST_NAME)], container.clock())

    response = await http.get(f"{API}/files/{FIRST_NAME}")
    assert response.status_code == 404


# --- расчёты ------------------------------------------------------------------


async def test_stats_over_the_whole_catalog_count_files(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats", json={"scope": "everything"})).json()
    assert body["files_selected"] == FILES_IN_CATALOG


async def test_stats_over_the_whole_catalog_sum_digits(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats", json={"scope": "everything"})).json()
    assert body["totals"]["total"] == DIGITS_IN_CATALOG


@pytest.mark.parametrize("digit", ALL_DIGITS)
async def test_stats_count_each_digit_across_the_catalog(stack, digit: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post(f"{API}/stats", json={"scope": "everything"})).json()
    assert body["totals"]["counts"][digit] == EACH_DIGIT_IN_CATALOG


async def test_stats_over_three_chosen_files(stack) -> None:
    """Три файла по 500 символов — полторы тысячи цифр."""
    http, _, _ = stack
    await download_everything(stack)

    chosen = ["file-0000.txt", "file-0001.txt", "file-0002.txt"]
    body = (await http.post(f"{API}/stats", json={"scope": "chosen", "names": chosen})).json()

    assert body["files_selected"] == 3
    assert body["totals"]["total"] == 1500


async def test_totals_cover_the_selection_even_when_breakdown_is_paginated(stack) -> None:
    """Общий итог — по всей выборке, разбивка — постранично."""
    http, _, _ = stack
    await download_everything(stack)

    body = (
        await http.post(f"{API}/stats", json={"scope": "everything"}, params={"size": 5})
    ).json()

    assert len(body["per_file"]) == 5
    assert body["pages"] == 5
    assert body["files_selected"] == FILES_IN_CATALOG
    assert body["totals"]["total"] == DIGITS_IN_CATALOG


@pytest.mark.parametrize(
    ("direction", "expected_first"),
    [("asc", FIRST_NAME), ("desc", LAST_NAME)],
)
async def test_stats_breakdown_is_sorted(stack, direction: str, expected_first: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (
        await http.post(
            f"{API}/stats",
            json={"scope": "everything"},
            params={"sort": "name", "dir": direction},
        )
    ).json()

    assert body["per_file"][0]["name"] == expected_first


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


async def test_started_run_is_accepted_and_running(stack) -> None:
    http, container, _ = stack

    response = await http.post(f"{API}/runs")
    await container.worker.wait()

    assert response.status_code == 202
    assert response.json()["status"] == "running"


async def test_finished_run_reports_the_whole_catalog(stack) -> None:
    http, container, _ = stack

    await http.post(f"{API}/runs")
    await container.worker.wait()

    body = (await http.get(f"{API}/runs/latest")).json()
    assert body["run"]["status"] == "completed"
    assert body["files_downloaded"] == FILES_IN_CATALOG
    assert body["names_discovered"] == FILES_IN_CATALOG
    assert body["percent"] == 100


async def test_starting_twice_returns_the_same_run(stack) -> None:
    """Идемпотентность не удобство: два обхода с одного адреса ускорили бы бан."""
    http, container, _ = stack

    first = (await http.post(f"{API}/runs")).json()
    second = (await http.post(f"{API}/runs")).json()
    await container.worker.wait()

    assert first["id"] == second["id"]


async def test_run_reports_pacing_without_refusals(stack) -> None:
    """Заглушка каталога не отказывает, поэтому отказов ровно ноль.

    Число запросов намеренно не сверяется с журналом вызовов клиента: как
    именно он их считает — деталь реализации, и тест, знающий её, сломается
    при первой же смене учёта, хотя поведение останется верным.
    """
    http, container, _ = stack

    await http.post(f"{API}/runs")
    await container.worker.wait()

    pacing = (await http.get(f"{API}/runs/latest")).json()["run"]["pacing"]
    assert pacing["throttle_events"] == 0
    assert pacing["seconds_paused"] == 0.0
    assert pacing["requests_made"] > 0
    assert pacing["requests_per_minute"] is not None


# --- документация -------------------------------------------------------------


async def test_openapi_documents_exactly_the_api_paths(stack) -> None:
    """Список путей выписан целиком: новый эндпоинт должен попасть сюда осознанно."""
    http, _, _ = stack
    paths = set((await http.get("/openapi.json")).json()["paths"])

    assert paths == {
        "/api/v1/files",
        "/api/v1/files/{name}",
        "/api/v1/stats",
        "/api/v1/runs",
        "/api/v1/runs/latest",
    }


async def test_docs_are_served(stack) -> None:
    http, _, _ = stack
    assert (await http.get("/docs")).status_code == 200
