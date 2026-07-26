"""Сценарий обхода каталога целиком — на подставных портах.

Здесь проверяется не «вызвался ли метод», а поведение саги: что каталог
скачивается до конца, что подтверждение никогда не опережает сохранение и что
повторная выдача уже известных имён не превращает цикл в бесконечный.
"""

from __future__ import annotations

import logging

import pytest

from app.application.download_service import (
    MAX_IDLE_ROUNDS,
    CatalogDownloadService,
    RunPauseObserver,
)
from app.application.errors import DownloadStalled
from app.application.launch_service import DownloadLauncher
from app.domain.file import FileStatus
from app.domain.pausing import Pause, PauseReason
from app.domain.run import RunStatus
from app.domain.values import MAX_FILES_PER_DOWNLOAD, FileContent, FileName
from tests.fakes import (
    FakeCatalogClient,
    InMemoryFileRepository,
    InMemoryRunRepository,
    StepClock,
    catalog_of,
)

CONFIRM_BATCH = 5


async def build(catalog: dict[str, str], **client_kwargs):
    files = InMemoryFileRepository()
    runs = InMemoryRunRepository()
    clock = StepClock()
    client = FakeCatalogClient(catalog, **client_kwargs)

    await DownloadLauncher(runs, clock).begin()
    service = CatalogDownloadService(
        files=files, runs=runs, client=client, clock=clock, confirm_batch_size=CONFIRM_BATCH
    )
    return service, files, runs, client


@pytest.mark.parametrize("catalog_size", [0, 1, 3, 4, 7, 23])
async def test_downloads_whole_catalog(catalog_size: int) -> None:
    catalog = catalog_of(catalog_size)
    service, files, runs, client = await build(catalog)

    await service.execute()

    assert set(files.files) == set(catalog)
    assert all(file.status is FileStatus.marked for file in files.files.values())
    assert client.confirmed == set(catalog)

    run = await runs.latest()
    assert run.status is RunStatus.completed


async def test_stops_when_names_endpoint_returns_empty() -> None:
    """Пустой список — единственный признак полноты: размер каталога не сообщается."""
    service, _, runs, client = await build(catalog_of(6))

    await service.execute()

    assert (await runs.latest()).status is RunStatus.completed
    # Последний запрос имён вернул пустую порцию.
    assert list(client.calls_of("names"))[-1].names == ()


async def test_confirmation_never_precedes_saving() -> None:
    """Главный инвариант саги.

    Подтверждение необратимо: подтвердить файл, содержимого которого нет в
    хранилище, — значит потерять его навсегда.
    """
    catalog = catalog_of(11)
    files = InMemoryFileRepository()
    runs = InMemoryRunRepository()
    clock = StepClock()

    class VerifyingClient(FakeCatalogClient):
        async def confirm_downloaded(self, names):
            for name in names:
                stored = files.files.get(name.value)
                assert stored is not None, f"{name} подтверждается, но его нет в хранилище"
                assert stored.has_content, f"{name} подтверждается без содержимого"
            return await super().confirm_downloaded(names)

    client = VerifyingClient(catalog)
    await DownloadLauncher(runs, clock).begin()
    service = CatalogDownloadService(
        files=files, runs=runs, client=client, clock=clock, confirm_batch_size=CONFIRM_BATCH
    )

    await service.execute()

    assert client.confirmed == set(catalog)


async def test_never_requests_more_than_the_catalog_allows() -> None:
    service, _, _, client = await build(catalog_of(17))

    await service.execute()

    oversized = [
        call for call in client.calls_of("download") if len(call.names) > MAX_FILES_PER_DOWNLOAD
    ]
    assert oversized == []


async def test_already_saved_names_are_not_downloaded_again() -> None:
    """Каталог повторно предлагает неподтверждённые имена — качать их заново незачем."""
    catalog = catalog_of(6)
    service, files, _, client = await build(catalog)

    # Файл уже скачан прошлым прогоном, но подтвердить его не успели.
    name = FileName(next(iter(catalog)))
    await files.register_discovered([name], StepClock()())
    files.files[name.value].attach_content(FileContent(catalog[name.value]), StepClock()())

    await service.execute()

    downloaded_names = [n for call in client.calls_of("download") for n in call.names]
    assert downloaded_names.count(name.value) == 0
    assert client.confirmed == set(catalog)


async def test_leftover_from_previous_run_is_confirmed_first() -> None:
    """Хвост прошлого прогона закрывается до того, как запрашиваются новые имена."""
    catalog = catalog_of(4)
    files = InMemoryFileRepository()
    runs = InMemoryRunRepository()
    clock = StepClock()

    name = FileName(next(iter(catalog)))
    await files.register_discovered([name], clock())
    files.files[name.value].attach_content(FileContent(catalog[name.value]), clock())

    client = FakeCatalogClient(catalog)
    await DownloadLauncher(runs, clock).begin()
    service = CatalogDownloadService(
        files=files, runs=runs, client=client, clock=clock, confirm_batch_size=CONFIRM_BATCH
    )

    await service.execute()

    assert client.calls[0].kind == "confirm"
    assert client.calls[0].names == (name.value,)


async def test_missing_file_does_not_take_the_others_with_it() -> None:
    """Отсутствующий в каталоге файл не должен ронять обход целиком.

    Размер каталога подобран так, чтобы все имена попали в одну порцию выдачи:
    иначе проверялось бы не поведение при отсутствующем файле, а то, сколько
    имён каталог успел показать до остановки.
    """
    catalog = catalog_of(FakeCatalogClient.NAMES_PER_PORTION)
    absent = next(iter(catalog))
    service, files, runs, _ = await build(catalog, missing={absent})

    with pytest.raises(DownloadStalled):
        await service.execute()

    survived = {name for name, file in files.files.items() if file.has_content}
    assert survived == set(catalog) - {absent}
    assert (await runs.latest()).status is RunStatus.failed


async def test_stalled_traversal_stops_instead_of_looping_forever() -> None:
    """Каталог предлагает имя, которое нельзя ни скачать, ни подтвердить.

    Без обнаружения затыка цикл крутился бы вечно: неподтверждённое имя
    возвращается в выдаче снова и снова.
    """
    catalog = catalog_of(1)
    absent = next(iter(catalog))
    service, _, _, client = await build(catalog, missing={absent})

    with pytest.raises(DownloadStalled) as failure:
        await service.execute()

    assert absent in str(failure.value)
    # Попыток было конечное число, а не бесконечное.
    assert len(list(client.calls_of("names"))) <= MAX_IDLE_ROUNDS + 1


async def test_pacing_is_recorded_on_the_run() -> None:
    service, _, runs, _ = await build(catalog_of(7))

    await service.execute()

    run = await runs.latest()
    # Заглушка не отказывает и держит интервал 1.0 — это её спецификация.
    # Точное число запросов задаёт алгоритм обхода, а не контракт, поэтому
    # проверяется факт записи замеров, а не совпадение с журналом вызовов.
    assert run.requests_made > 0
    assert run.throttle_events == 0
    assert run.seconds_paused == 0.0
    assert run.interval_seconds == 1.0
    assert run.requests_per_minute is not None


async def test_pacing_accumulates_across_a_restart() -> None:
    """Прогон переживает рестарт — счётчики обязаны пережить его вместе с ним.

    Клиент считает запросы от собственного старта, поэтому без базовой точки
    возобновлённый обход обнулил бы итог.
    """
    catalog = catalog_of(9)
    files = InMemoryFileRepository()
    runs = InMemoryRunRepository()
    clock = StepClock()

    run = await DownloadLauncher(runs, clock).begin()
    run.record_pacing(requests=100, throttles=3, paused=42.0, interval=2.0)
    await runs.save(run)

    client = FakeCatalogClient(catalog)
    service = CatalogDownloadService(
        files=files, runs=runs, client=client, clock=clock, confirm_batch_size=CONFIRM_BATCH
    )
    await service.execute()

    finished = await runs.latest()
    # База сохранена и к ней прибавлено: равенство ста означало бы обнуление —
    # ровно ту ошибку, ради которой базовая точка и заведена.
    assert finished.requests_made > 100
    assert finished.throttle_events == 3
    assert finished.seconds_paused == 42.0


async def test_second_launch_returns_the_same_run() -> None:
    """Повторное нажатие кнопки не должно плодить параллельные обходы."""
    runs = InMemoryRunRepository()
    clock = StepClock()
    launcher = DownloadLauncher(runs, clock)

    first = await launcher.begin()
    second = await launcher.begin()

    assert first is second
    assert len(runs.runs) == 1


async def test_new_run_starts_after_previous_finished() -> None:
    runs = InMemoryRunRepository()
    clock = StepClock()
    launcher = DownloadLauncher(runs, clock)

    first = await launcher.begin()
    first.complete(clock())
    await runs.save(first)

    second = await launcher.begin()
    assert second is not first
    assert second.is_active


@pytest.mark.parametrize(
    "reason", [PauseReason.rate_limited, PauseReason.banned, PauseReason.network_error]
)
async def test_pause_observer_records_reason_on_the_run(reason: PauseReason) -> None:
    runs = InMemoryRunRepository()
    clock = StepClock()
    run = await DownloadLauncher(runs, clock).begin()

    await RunPauseObserver(runs, clock).on_wait(Pause(reason, seconds=42))

    assert run.pause_reason is reason
    assert run.paused_until is not None


async def test_pause_observer_is_silent_without_active_run() -> None:
    runs = InMemoryRunRepository()
    observer = RunPauseObserver(runs, StepClock())
    await observer.on_wait(Pause(PauseReason.banned, seconds=1))  # не должно упасть


async def test_failure_marks_run_as_failed() -> None:
    catalog = catalog_of(3)
    files = InMemoryFileRepository()
    runs = InMemoryRunRepository()
    clock = StepClock()

    class BrokenClient(FakeCatalogClient):
        async def fetch_names(self):
            raise RuntimeError("каталог недоступен")

    await DownloadLauncher(runs, clock).begin()
    service = CatalogDownloadService(
        files=files,
        runs=runs,
        client=BrokenClient(catalog),
        clock=clock,
        confirm_batch_size=CONFIRM_BATCH,
    )

    with pytest.raises(RuntimeError):
        await service.execute()

    run = await runs.latest()
    assert run.status is RunStatus.failed
    assert "каталог недоступен" in run.error


async def test_empty_catalog_from_the_start_is_flagged(caplog) -> None:
    """Пустой каталог на старте — почти всегда использованный идентификатор.

    Формально это успешное завершение, но молча отрапортовать «скачано всё»
    при нуле файлов значит отправить человека искать несуществующую поломку.
    """
    service, _, runs, _ = await build({})

    with caplog.at_level(logging.WARNING, logger="app.application.download_service"):
        await service.execute()

    assert (await runs.latest()).status is RunStatus.completed
    assert any(getattr(record, "event", None) == "catalog.empty" for record in caplog.records)


async def test_non_empty_catalog_is_not_flagged(caplog) -> None:
    service, _, _, _ = await build(catalog_of(3))

    with caplog.at_level(logging.WARNING, logger="app.application.download_service"):
        await service.execute()

    assert not any(getattr(record, "event", None) == "catalog.empty" for record in caplog.records)


async def test_completion_log_reports_persisted_numbers(caplog) -> None:
    """Итог берётся из прогона и хранилища, а не из счётчиков в памяти."""
    service, _, _, _ = await build(catalog_of(7))

    with caplog.at_level(logging.INFO, logger="app.application.download_service"):
        await service.execute()

    entry = next(
        record for record in caplog.records if getattr(record, "event", None) == "run.completed"
    )
    assert entry.files == 7
    assert entry.throttle_events == 0
    assert entry.requests_made > 0
