"""Сценарий обхода каталога.

Порядок шагов — это и есть защита от потери данных:

    получили имена → скачали → сохранили → и только теперь подтвердили серверу

Подтверждение необратимо, поэтому оно вынесено в отдельный шаг, который читает
уже сохранённые файлы из хранилища. Подтвердить то, чего нет в базе, при таком
устройстве невозможно — не потому что «мы помним про порядок», а потому что
данные для подтверждения берутся из самого хранилища.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

from app.application.errors import DownloadStalled, FileMissingFromCatalog
from app.application.ports import (
    CatalogClient,
    FileRepository,
    PacingSnapshot,
    RunRepository,
)
from app.domain.errors import DomainError
from app.domain.pausing import Pause
from app.domain.run import SECONDS_IN_MINUTE, DownloadRun
from app.domain.values import MAX_FILES_PER_DOWNLOAD, FileName

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

#: Сколько кругов подряд без единого скачанного или подтверждённого файла
#: считаем достаточным основанием признать обход застрявшим.
MAX_IDLE_ROUNDS = 3


class RunPauseObserver:
    """Переносит вынужденные паузы клиента в состояние прогона.

    Идентификатор прогона не нужен: активный прогон в системе всегда один.
    Счётчики пауз здесь не ведутся: их считает клиент и отдаёт в ``pacing()``,
    откуда они попадают в сам прогон. Второй счёт того же события — это второй
    источник правды, который рано или поздно разойдётся с первым.
    """

    def __init__(self, runs: RunRepository, clock: Clock) -> None:
        self._runs = runs
        self._clock = clock

    async def on_wait(self, pause: Pause) -> None:
        run = await self._runs.active()
        if run is None:
            return
        run.pause(pause, self._clock())
        await self._runs.save(run)


class CatalogDownloadService:
    """Скачивает каталог целиком, пока ручка имён не вернёт пустой список."""

    def __init__(
        self,
        *,
        files: FileRepository,
        runs: RunRepository,
        client: CatalogClient,
        clock: Clock,
        confirm_batch_size: int,
    ) -> None:
        self._files = files
        self._runs = runs
        self._client = client
        self._clock = clock
        self._confirm_batch_size = confirm_batch_size
        self._baseline = PacingSnapshot.zero()

    async def execute(self) -> None:
        """Отработать активный прогон до конца каталога."""
        run = await self._runs.active()
        if run is None:
            logger.info("активного прогона нет, скачивать нечего")
            return

        # Точка отсчёта: то, что прогон накопил до этого запуска процесса.
        self._baseline = PacingSnapshot(
            requests_made=run.requests_made,
            throttle_events=run.throttle_events,
            seconds_paused=run.seconds_paused,
            interval_seconds=run.interval_seconds or 0.0,
        )

        try:
            await self._traverse(run)
        except Exception as exc:
            logger.exception("прогон #%s прерван ошибкой", run.id)
            await self._mark_failed(run, f"{type(exc).__name__}: {exc}")
            raise

    async def _traverse(self, run: DownloadRun) -> None:
        # Хвост прошлого прогона: сохранено у нас, но серверу не подтверждено.
        await self._confirm_saved()
        idle_rounds = 0
        first_round = True

        while True:
            names = await self._client.fetch_names()
            run.record_activity(self._clock())
            await self._save_with_pacing(run)

            if not names and first_round and await self._files.count_with_content() == 0:
                # Каталог пуст с самого начала: почти всегда это значит, что
                # идентификатор кандидата уже использован. Молча завершиться
                # «успешно» с нулём файлов — худший из возможных ответов.
                logger.warning(
                    "каталог не отдал ни одного имени: скорее всего "
                    "CATALOG_API__CANDIDATE_ID уже использован, нужен новый",
                    extra={"event": "catalog.empty"},
                )
            first_round = False

            if not names:
                # Пустой список — единственный доступный признак полноты:
                # общее число файлов каталог не сообщает.
                await self._confirm_saved()
                run.complete(self._clock())
                await self._save_with_pacing(run)
                await self._log_completion(run)
                return

            await self._files.register_discovered(names, self._clock())
            pending = await self._files.names_without_content(names)

            if not pending:
                # Каталог предлагает то, что у нас уже есть, — значит мы отстали
                # с подтверждениями. Без этого шага цикл не кончится.
                progress = await self._confirm_saved()
            else:
                progress = 0
                for chunk in _chunks(pending, MAX_FILES_PER_DOWNLOAD):
                    progress += await self._fetch_and_store(chunk)
                    run.record_activity(self._clock())
                    await self._save_with_pacing(run)

                if await self._files.count_awaiting_confirmation() >= self._confirm_batch_size:
                    await self._confirm_saved()

            # Каталог продолжает предлагать имена, но ни скачать, ни
            # подтвердить их не выходит: без этой проверки обход крутился бы
            # вечно, потому что неподтверждённое имя возвращается в выдаче.
            idle_rounds = 0 if progress else idle_rounds + 1
            if idle_rounds >= MAX_IDLE_ROUNDS:
                raise DownloadStalled([str(name) for name in names])

    async def _fetch_and_store(self, names: list[FileName]) -> int:
        """Скачать пачку и сохранить. Возвращает число сохранённых файлов."""
        try:
            contents = await self._client.fetch_contents(names)
        except FileMissingFromCatalog as exc:
            # Клиент уже изолировал отсутствующие имена поштучно. Ронять из-за
            # них весь обход незачем — остальные файлы важнее.
            logger.error(
                "пропускаю отсутствующие в каталоге файлы: %s",
                exc.names,
                extra={"event": "files.missing", "names": exc.names},
            )
            return 0

        if not contents:
            return 0

        stored = {file.name: file for file in await self._files.get_many(list(contents))}
        now = self._clock()
        updated = []
        for name, content in contents.items():
            file = stored.get(name)
            if file is None or file.is_confirmed:
                continue
            try:
                file.attach_content(content, now)
            except DomainError as exc:
                logger.error("не сохраняю %s: %s", name, exc)
                continue
            updated.append(file)

        await self._files.save_many(updated)
        return len(updated)

    async def _confirm_saved(self) -> int:
        """Подтвердить серверу всё, что уже надёжно сохранено у нас.

        Возвращает число подтверждённых файлов — по нему обход понимает,
        продвинулся ли он вообще.
        """
        confirmed = 0
        while True:
            batch = await self._files.awaiting_confirmation(self._confirm_batch_size)
            if not batch:
                return confirmed

            outcome = await self._client.confirm_downloaded([file.name for file in batch])
            logger.info(
                "подтверждено %s файлов (ранее уже были отмечены: %s)",
                outcome.marked_now,
                outcome.already_marked,
                extra={
                    "event": "files.confirmed",
                    "marked_now": outcome.marked_now,
                    "already_marked": outcome.already_marked,
                },
            )

            now = self._clock()
            for file in batch:
                file.confirm_marked(now)
            await self._files.save_many(batch)
            confirmed += len(batch)

    async def _save_with_pacing(self, run: DownloadRun) -> None:
        """Сохранить прогон вместе со свежими замерами темпа.

        Клиент считает запросы от собственного старта, а прогон переживает
        рестарт процесса. Поэтому к его цифрам прибавляется то, что прогон
        накопил до нас, — иначе после возобновления счётчики обнулились бы и
        итог описывал бы только последний запуск.
        """
        snapshot = self._client.pacing()
        run.record_pacing(
            requests=self._baseline.requests_made + snapshot.requests_made,
            throttles=self._baseline.throttle_events + snapshot.throttle_events,
            paused=self._baseline.seconds_paused + snapshot.seconds_paused,
            interval=snapshot.interval_seconds,
        )
        await self._runs.save(run)

    async def _log_completion(self, run: DownloadRun) -> None:
        """Итог обхода одной записью.

        Все числа берутся из прогона и хранилища, а не из счётчиков в памяти:
        обход переживает рестарт процесса, и счётчики, начавшиеся заново,
        описали бы только последний запуск — строка «скачано 155» при 1234
        файлах уже случалась.
        """
        logger.info(
            "прогон #%s: каталог скачан полностью",
            run.id,
            extra={
                "event": "run.completed",
                "run_id": run.id,
                "files": await self._files.count_with_content(),
                "requests_made": run.requests_made,
                "throttle_events": run.throttle_events,
                "minutes_paused": round(run.seconds_paused / SECONDS_IN_MINUTE, 1),
                "requests_per_minute": round(run.requests_per_minute or 0),
            },
        )

    async def _mark_failed(self, run: DownloadRun, error: str) -> None:
        """Пометить прогон неуспешным.

        Работаем с тем объектом, что уже держим: перечитывание из хранилища
        вернуло бы копию без сделанных в памяти изменений, а после успешного
        завершения — вообще ничего.
        """
        if not run.is_active:
            return
        run.fail(error, self._clock())
        await self._runs.save(run)


def _chunks(items: Sequence[FileName], size: int) -> list[list[FileName]]:
    return [list(items[index : index + size]) for index in range(0, len(items), size)]
