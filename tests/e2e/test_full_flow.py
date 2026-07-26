"""Сквозной сценарий: веб-интерфейс на полном стеке.

Стенд — в ``conftest.py`` этого каталога: приложение, база, маршруты и шаблоны
настоящие, подменён только клиент каталога.
"""

from __future__ import annotations

import html
import re
from contextlib import asynccontextmanager

import pytest

from app.container import Container
from app.domain.digits import DIGITS
from app.main import create_app
from tests.e2e.conftest import (
    CATALOG_SIZE,
    EXPECTED_PER_DIGIT_PER_FILE,
    PAGE_SIZE,
    download_everything,
)
from tests.fakes import FakeCatalogClient


def urls_of(body: str) -> str:
    """Разметка с раскодированными сущностями.

    В атрибутах «&» приходит как «&amp;» — это корректный HTML, но сверять
    адреса удобнее в исходном виде.
    """
    return html.unescape(body)


def file_names_in(body: str) -> list[str]:
    return re.findall(r"<code>([^<]+)</code>", body)


async def test_button_downloads_the_whole_catalog(stack, catalog) -> None:
    http, _, client = stack

    await download_everything(stack)

    assert client.confirmed == set(catalog)

    page = (await http.get("/")).text
    assert "каталог скачан полностью" in page
    assert f"{CATALOG_SIZE} из {CATALOG_SIZE}" in page


async def test_health_reports_readiness(stack) -> None:
    http, _, _ = stack
    response = await http.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["download"] == "never_started"


async def test_health_reflects_finished_run(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get("/health")).json()
    assert body["download"] == "completed"
    assert body["worker_running"] is False


async def test_every_response_carries_a_request_id(stack) -> None:
    http, _, _ = stack
    response = await http.get("/")
    assert response.headers["x-request-id"]


async def test_progress_page_before_start(stack) -> None:
    http, _, _ = stack
    page = (await http.get("/")).text
    assert "не запускался" in page
    assert "Скачать данные" in page


async def test_second_press_does_not_start_a_parallel_run(stack) -> None:
    http, container, _ = stack

    await http.post("/download/start", follow_redirects=False)
    await http.post("/download/start", follow_redirects=False)
    await container.worker.wait()

    runs = await container.runs.latest()
    assert runs.id == 1


async def test_progress_fragment_stops_polling_when_finished(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    fragment = (await http.get("/download/progress")).text
    assert "hx-trigger" not in fragment


async def test_files_page_lists_and_paginates(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    first = (await http.get("/files")).text
    assert f"всего файлов: {CATALOG_SIZE}" in first
    assert first.count('data-role="file-checkbox"') == PAGE_SIZE

    last_page_number = -(-CATALOG_SIZE // PAGE_SIZE)
    last = (await http.get(f"/files?page={last_page_number}")).text
    assert last.count('data-role="file-checkbox"') == CATALOG_SIZE % PAGE_SIZE


async def test_selection_mode_survives_pagination(stack) -> None:
    """Режим «весь каталог» живёт в адресе.

    Пагинация — обычная навигация с перезагрузкой: состояние, которое хранилось
    бы только в DOM, терялось бы при первом же переходе.
    """
    http, _, _ = stack
    await download_everything(stack)

    body = urls_of((await http.get("/files?scope=everything")).text)

    # Переключатель отрисован включённым...
    assert "switch--on" in body
    assert "checked>" in body
    # ...и все переходы несут режим дальше.
    assert "scope=everything" in body
    assert "scope=chosen" not in body


async def test_pagination_returns_only_the_list_fragment(stack) -> None:
    """Страница не перезагружается — иначе посчитанная статистика пропадала бы."""
    http, _, _ = stack
    await download_everything(stack)

    fragment = await http.get("/files?page=2", headers={"HX-Request": "true"})
    body = fragment.text

    assert body.lstrip().startswith('<div id="file-list"')
    assert "<html" not in body
    # Блок статистики остаётся нетронутым, потому что его в ответе нет.
    assert 'id="statistics"' not in body


async def test_history_restore_returns_the_whole_page(stack) -> None:
    """Кнопка «назад» должна вернуть страницу, а не голый фрагмент."""
    http, _, _ = stack
    await download_everything(stack)

    response = await http.get(
        "/files?page=2",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )

    assert "<html" in response.text
    assert 'id="statistics"' in response.text


async def test_plain_navigation_returns_the_whole_page(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get("/files?page=2")).text
    assert "<html" in body
    assert 'id="file-list"' in body


async def test_default_scope_is_carried_in_links(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get("/files")).text
    assert "scope=chosen" in body
    assert "switch--on" not in body


async def test_unknown_scope_is_rejected(stack) -> None:
    """Область выбора — перечисление, а не свободная строка."""
    http, _, _ = stack
    response = await http.get("/files?scope=whatever")
    assert response.status_code == 422


@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_files_page_accepts_both_orders(stack, order: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    response = await http.get(f"/files?dir={order}")
    assert response.status_code == 200


async def test_unknown_order_falls_back_instead_of_failing(stack) -> None:
    """Мусор в параметрах сортировки — не повод отдавать ошибку вместо страницы."""
    http, _, _ = stack
    await download_everything(stack)

    response = await http.get("/files?sort=сикось&dir=накось")
    assert response.status_code == 200
    # Молча вернулись к умолчанию: сортировка по времени, сначала новые.
    assert "sort=downloaded_at" in response.text


@pytest.mark.parametrize("field", ["name", "downloaded_at"])
async def test_both_columns_are_sortable(stack, field: str) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.get(f"/files?sort={field}&dir=asc")).text

    # Стрелка ровно одна — у столбца, по которому идёт сортировка.
    assert body.count('class="th-sort__arrow">↑') == 1
    assert body.count('class="th-sort__arrow">↓') == 0
    # Повторное нажатие перевернёт направление.
    assert f"sort={field}&dir=desc" in urls_of(body)


async def test_name_sorting_actually_reorders_rows(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    ascending = file_names_in((await http.get("/files?sort=name&dir=asc")).text)
    descending = file_names_in((await http.get("/files?sort=name&dir=desc")).text)

    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)
    assert ascending[0] != descending[0]


async def test_statistics_table_is_sortable_too(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    ascending = file_names_in(
        (await http.post("/stats?sort=name&dir=asc", data={"scope": "everything"})).text
    )
    descending = file_names_in(
        (await http.post("/stats?sort=name&dir=desc", data={"scope": "everything"})).text
    )

    assert ascending == sorted(ascending)
    assert descending == sorted(descending, reverse=True)


async def test_statistics_pagination_keeps_the_chosen_order(stack) -> None:
    """Переход по страницам разбивки не должен сбрасывать сортировку."""
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post("/stats?sort=name&dir=desc", data={"scope": "everything"})).text
    assert "page=2&sort=name&dir=desc" in urls_of(body)


async def test_statistics_for_the_whole_catalog(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    response = await http.post("/stats", data={"scope": "everything"})
    body = response.text

    assert response.status_code == 200
    assert f"Выбрано файлов: <strong>{CATALOG_SIZE}</strong>" in body
    expected_total = EXPECTED_PER_DIGIT_PER_FILE * len(DIGITS) * CATALOG_SIZE
    assert str(expected_total) in body


async def test_statistics_for_a_chosen_subset(stack, catalog) -> None:
    http, _, _ = stack
    await download_everything(stack)

    chosen = sorted(catalog)[:3]
    response = await http.post("/stats", data={"scope": "chosen", "names": chosen})
    body = response.text

    assert "Выбрано файлов: <strong>3</strong>" in body
    for name in chosen:
        assert name in body


async def test_statistics_without_selection_explains_itself(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post("/stats", data={"scope": "chosen"})).text
    assert "Не выбрано ни одного файла" in body


async def test_per_file_table_is_paginated(stack) -> None:
    """Разбивка по файлам листается, а не обрезается на первых N строках."""
    http, _, _ = stack
    await download_everything(stack)

    body = (await http.post("/stats", data={"scope": "everything"})).text
    expected_pages = -(-CATALOG_SIZE // PAGE_SIZE)

    assert body.count("<code>") == PAGE_SIZE
    assert f"из {expected_pages}" in body
    # Общий итог всё равно по всей выборке, а не по видимой странице.
    assert f"Выбрано файлов: <strong>{CATALOG_SIZE}</strong>" in body


async def test_last_page_of_statistics_holds_the_remainder(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    last_page = -(-CATALOG_SIZE // PAGE_SIZE)
    body = (await http.post(f"/stats?page={last_page}", data={"scope": "everything"})).text

    assert body.count("<code>") == CATALOG_SIZE % PAGE_SIZE


async def test_statistics_rows_are_numbered_continuously(stack) -> None:
    """Нумерация сквозная: на второй странице она продолжается, а не начинается заново."""
    http, _, _ = stack
    await download_everything(stack)

    second = (await http.post("/stats?page=2", data={"scope": "everything"})).text
    assert f'<td class="is-num note">{PAGE_SIZE + 1}</td>' in second


async def test_statistics_pages_do_not_repeat_files(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    seen: list[str] = []
    for page in range(1, -(-CATALOG_SIZE // PAGE_SIZE) + 1):
        body = (await http.post(f"/stats?page={page}", data={"scope": "everything"})).text
        seen.extend(file_names_in(body))

    assert len(seen) == CATALOG_SIZE
    assert len(set(seen)) == CATALOG_SIZE


async def test_statistics_page_number_is_clamped(stack) -> None:
    http, _, _ = stack
    await download_everything(stack)

    response = await http.post("/stats?page=9999", data={"scope": "everything"})
    assert response.status_code == 200
    assert f"из {-(-CATALOG_SIZE // PAGE_SIZE)}" in response.text


async def test_interrupted_run_is_resumed_on_startup(sessions, settings, catalog) -> None:
    """Рестарт сервиса не должен обнулять многочасовой обход."""
    client = FakeCatalogClient(catalog)

    @asynccontextmanager
    async def catalog_client(_settings, _observer):
        yield client

    # Первый запуск: создаём прогон, но не даём ему отработать.
    starting = Container(settings, session_factory=sessions, catalog_client=catalog_client)
    await starting.begin_download()
    await starting.worker.stop()

    assert (await starting.runs.active()) is not None

    # Второй запуск процесса подхватывает незавершённый прогон сам.
    resumed = Container(settings, session_factory=sessions, catalog_client=catalog_client)
    application = create_app(resumed)
    async with application.router.lifespan_context(application):
        await resumed.worker.wait()

    assert client.confirmed == set(catalog)
    assert (await resumed.runs.latest()).status.is_terminal
