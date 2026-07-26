"""Репозитории и запросы чтения против настоящего Postgres."""

from __future__ import annotations

import pytest

from app.domain.digits import DigitCounts
from app.domain.file import FileStatus
from app.domain.run import DownloadRun, RunStatus
from app.domain.selection import Selection
from app.domain.sorting import SortDirection, SortField, Sorting
from app.domain.values import FileContent, FileName
from app.infrastructure.persistence.queries import SqlAlchemyCatalogQueries
from app.infrastructure.persistence.repositories import (
    SqlAlchemyFileRepository,
    SqlAlchemyRunRepository,
)
from tests.fakes import START


@pytest.fixture
def files(sessions) -> SqlAlchemyFileRepository:
    return SqlAlchemyFileRepository(sessions)


@pytest.fixture
def runs(sessions) -> SqlAlchemyRunRepository:
    return SqlAlchemyRunRepository(sessions)


@pytest.fixture
def queries(sessions) -> SqlAlchemyCatalogQueries:
    return SqlAlchemyCatalogQueries(sessions)


def names(*values: str) -> list[FileName]:
    return [FileName(value) for value in values]


async def store(files: SqlAlchemyFileRepository, name: str, content: str) -> None:
    await files.register_discovered(names(name), START)
    stored = (await files.get_many(names(name)))[0]
    stored.attach_content(FileContent(content), START)
    await files.save_many([stored])


# --- файлы --------------------------------------------------------------------


async def test_discovered_names_are_idempotent(files) -> None:
    """Каталог повторно предлагает те же имена — это норма, а не повод для сбоя."""
    await files.register_discovered(names("a.txt", "b.txt"), START)
    await files.register_discovered(names("b.txt", "c.txt"), START)

    stored = await files.get_many(names("a.txt", "b.txt", "c.txt"))
    assert len(stored) == 3


async def test_repeated_registration_does_not_wipe_content(files) -> None:
    await store(files, "a.txt", "12345")
    await files.register_discovered(names("a.txt"), START)

    stored = (await files.get_many(names("a.txt")))[0]
    assert stored.has_content
    assert stored.status is FileStatus.downloaded


async def test_value_objects_survive_a_round_trip(files) -> None:
    await store(files, "a.txt", "9876543210")
    stored = (await files.get_many(names("a.txt")))[0]

    assert isinstance(stored.name, FileName)
    assert isinstance(stored.content, FileContent)
    assert stored.counts == DigitCounts.from_content("9876543210")


async def test_names_without_content(files) -> None:
    await files.register_discovered(names("a.txt", "b.txt"), START)
    await store(files, "a.txt", "111")

    pending = await files.names_without_content(names("a.txt", "b.txt"))
    assert pending == names("b.txt")


async def test_awaiting_confirmation_excludes_confirmed(files) -> None:
    await store(files, "a.txt", "111")
    await store(files, "b.txt", "222")

    assert await files.count_awaiting_confirmation() == 2

    batch = await files.awaiting_confirmation(limit=10)
    for file in batch:
        file.confirm_marked(START)
    await files.save_many(batch)

    assert await files.count_awaiting_confirmation() == 0
    assert await files.awaiting_confirmation(limit=10) == []


@pytest.mark.parametrize("limit", [1, 2, 5])
async def test_awaiting_confirmation_respects_limit(files, limit: int) -> None:
    for index in range(5):
        await store(files, f"file-{index}.txt", "1234567890")

    assert len(await files.awaiting_confirmation(limit=limit)) == min(limit, 5)


async def test_empty_inputs_do_not_touch_the_database(files) -> None:
    await files.register_discovered([], START)
    await files.save_many([])
    assert await files.names_without_content([]) == []
    assert await files.get_many([]) == []


# --- прогоны ------------------------------------------------------------------


async def test_run_lifecycle_is_persisted(runs) -> None:
    run = await runs.add(DownloadRun.start(START))
    assert run.id is not None
    assert (await runs.active()).id == run.id

    run.complete(START)
    await runs.save(run)

    assert await runs.active() is None
    assert (await runs.latest()).status is RunStatus.completed


async def test_latest_returns_the_newest_run(runs) -> None:
    first = await runs.add(DownloadRun.start(START))
    first.complete(START)
    await runs.save(first)
    second = await runs.add(DownloadRun.start(START))

    assert (await runs.latest()).id == second.id
    assert (await runs.active()).id == second.id


async def test_no_runs_yet(runs) -> None:
    assert await runs.active() is None
    assert await runs.latest() is None


# --- чтение -------------------------------------------------------------------


async def test_counters(files, queries) -> None:
    await files.register_discovered(names("a.txt", "b.txt", "c.txt"), START)
    await store(files, "a.txt", "111")

    assert await queries.names_discovered() == 3
    assert await queries.files_with_content() == 1


async def store_timed(files, name: str, minutes: int) -> None:
    from datetime import timedelta

    await files.register_discovered(names(name), START)
    stored = (await files.get_many(names(name)))[0]
    stored.attach_content(FileContent("1"), START + timedelta(minutes=minutes))
    await files.save_many([stored])


@pytest.mark.parametrize(
    ("sorting", "expected_first"),
    [
        (Sorting(SortField.downloaded_at, SortDirection.descending), "c.txt"),
        (Sorting(SortField.downloaded_at, SortDirection.ascending), "a.txt"),
        # Имя сортируется независимо от времени скачивания: здесь порядок
        # алфавитный обратен хронологическому, и это должно быть видно.
        (Sorting(SortField.name, SortDirection.ascending), "a.txt"),
        (Sorting(SortField.name, SortDirection.descending), "c.txt"),
    ],
    ids=["time-desc", "time-asc", "name-asc", "name-desc"],
)
async def test_page_ordering(files, queries, sorting: Sorting, expected_first: str) -> None:
    for index, name in enumerate(("a.txt", "b.txt", "c.txt")):
        await store_timed(files, name, index)

    page = await queries.page(number=1, size=10, sorting=sorting)
    assert str(page.records[0].name) == expected_first


async def test_name_ordering_is_independent_of_time(files, queries) -> None:
    """Имена по алфавиту, даже когда время скачивания идёт вразнобой."""
    for name, minutes in (("c.txt", 0), ("a.txt", 1), ("b.txt", 2)):
        await store_timed(files, name, minutes)

    page = await queries.page(
        number=1, size=10, sorting=Sorting(SortField.name, SortDirection.ascending)
    )
    assert [str(record.name) for record in page.records] == ["a.txt", "b.txt", "c.txt"]


async def test_equal_timestamps_keep_a_stable_order(files, queries) -> None:
    """Одинаковое время — устойчивый порядок по имени.

    Без дополнительного ключа файлы раскладывались бы по страницам произвольно,
    и один и тот же мог бы попасть на две страницы сразу.
    """
    for name in ("c.txt", "a.txt", "b.txt"):
        await store_timed(files, name, 0)

    page = await queries.page(
        number=1, size=10, sorting=Sorting(SortField.downloaded_at, SortDirection.descending)
    )
    assert [str(record.name) for record in page.records] == ["a.txt", "b.txt", "c.txt"]


@pytest.mark.parametrize(
    ("total", "size", "expected_pages"),
    [(0, 10, 1), (1, 10, 1), (10, 10, 1), (11, 10, 2), (25, 10, 3)],
)
async def test_pagination_arithmetic(files, queries, total, size, expected_pages) -> None:
    for index in range(total):
        await store(files, f"file-{index:03d}.txt", "1234567890")

    page = await queries.page(number=1, size=size, sorting=Sorting())
    assert page.pages == expected_pages
    assert page.total == total


async def test_page_number_is_clamped(files, queries) -> None:
    await store(files, "a.txt", "1")
    page = await queries.page(number=999, size=10, sorting=Sorting())
    assert page.number == 1


async def test_digit_totals_over_selection(files, queries) -> None:
    await store(files, "a.txt", "111")
    await store(files, "b.txt", "22")
    await store(files, "c.txt", "3")

    everything = await queries.digit_totals(Selection.everything())
    assert everything.as_dict()["1"] == 3
    assert everything.as_dict()["2"] == 2
    assert everything.as_dict()["3"] == 1

    chosen = await queries.digit_totals(Selection.of(["a.txt", "b.txt"]))
    assert chosen.as_dict()["1"] == 3
    assert chosen.as_dict()["3"] == 0


async def test_totals_ignore_files_without_content(files, queries) -> None:
    await store(files, "a.txt", "111")
    await files.register_discovered(names("pending.txt"), START)

    assert await queries.count_selected(Selection.everything()) == 1
    assert (await queries.digit_totals(Selection.everything())).total == 3


async def test_empty_selection_totals_are_zero_not_null(files, queries) -> None:
    totals = await queries.digit_totals(Selection.of([]))
    assert totals.total == 0


async def test_per_file_digits(files, queries) -> None:
    await store(files, "a.txt", "1122")
    rows = await queries.per_file_digits(Selection.everything(), sorting=Sorting(), limit=10)

    assert len(rows) == 1
    assert str(rows[0].name) == "a.txt"
    assert rows[0].counts.as_dict()["1"] == 2


@pytest.mark.parametrize("limit", [1, 3, 10])
async def test_per_file_digits_respect_limit(files, queries, limit: int) -> None:
    for index in range(5):
        await store(files, f"file-{index}.txt", "12345")

    rows = await queries.per_file_digits(Selection.everything(), sorting=Sorting(), limit=limit)
    assert len(rows) == min(limit, 5)
