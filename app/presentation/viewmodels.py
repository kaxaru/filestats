"""ViewModel'и — то, что видит шаблон.

Шаблон получает готовые к отрисовке значения и не содержит ни вычислений, ни
обращений к домену: перевод времени в НСК, подписи статусов, проценты и ссылки
пагинации собираются здесь. Так разметка остаётся разметкой, а логика
представления — обычным кодом, который можно проверить тестом.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from app.application.ports import FilePage
from app.application.progress_service import DownloadProgress
from app.application.stats_service import CatalogStatistics
from app.domain.digits import DIGITS, DigitCounts
from app.domain.run import SECONDS_IN_MINUTE, DownloadRun, RunStatus
from app.domain.sorting import SortField, Sorting
from app.presentation.formatting import MomentFormatter, percent, plural
from app.presentation.scope import SelectionScope

_STATUS_LABELS = {
    RunStatus.running: "идёт скачивание",
    RunStatus.completed: "каталог скачан полностью",
    RunStatus.failed: "ошибка",
    RunStatus.stopped: "остановлен",
}

_HINTS = {
    RunStatus.running: (
        "Общее число файлов каталог не сообщает, поэтому «получено названий» "
        "растёт по ходу работы. Процесс завершится, когда ручка имён вернёт "
        "пустой список."
    ),
    RunStatus.completed: "Ручка имён вернула пустой список — каталог скачан полностью.",
    RunStatus.stopped: "Процесс остановлен, прогресс сохранён.",
}

_EMPTY_CATALOG_HINT = (
    "Каталог не отдал ни одного имени. Почти всегда это значит, что значение "
    "CATALOG_API__CANDIDATE_ID уже использовано: каталог выдаёт каждый файл "
    "ровно один раз на идентификатор. Укажите в .env любую другую уникальную "
    "строку и запустите снова."
)


def hint_for(status: RunStatus, files_with_content: int) -> str | None:
    """Подсказка под показателями.

    Завершение с нулём файлов формально успешно, но почти всегда означает
    использованный идентификатор. Сообщить «каталог скачан полностью», когда
    не скачано ничего, — верный способ отправить человека искать несуществующую
    поломку.
    """
    if status is RunStatus.completed and files_with_content == 0:
        return _EMPTY_CATALOG_HINT
    return _HINTS.get(status)


@dataclass(frozen=True, slots=True)
class PacingViewModel:
    """Наблюдавшийся темп обращения к каталогу.

    Именно наблюдавшийся, а не заявленный: каталог свои лимиты не публикует и
    не отдаёт заголовков вида ``X-RateLimit-*``. Всё, что здесь показано, —
    измерения собственного трафика сервиса.
    """

    requests_made: int
    requests_per_minute: str
    throttle_events: int
    interval_seconds: str
    minutes_paused: str
    verdict: str

    @classmethod
    def build(cls, run: DownloadRun) -> Self | None:
        if not run.has_pacing_data:
            return None

        rate = run.requests_per_minute
        return cls(
            requests_made=run.requests_made,
            requests_per_minute=f"{rate:.0f}" if rate else "—",
            throttle_events=run.throttle_events,
            interval_seconds=(f"{run.interval_seconds:.2f}" if run.interval_seconds else "—"),
            minutes_paused=f"{run.seconds_paused / SECONDS_IN_MINUTE:.1f}",
            verdict=_pacing_verdict(run.throttle_events, rate),
        )


def _pacing_verdict(throttle_events: int, rate: float | None) -> str:
    """Словами: удалось ли нащупать границу, не переходя её."""
    if rate is None:
        return "Данных пока недостаточно."

    per_minute = round(rate)
    requests = plural(per_minute, "запрос", "запроса", "запросов")

    if throttle_events == 0:
        return (
            f"Отказов не было: темп ≈{per_minute} {requests} в минуту каталог "
            "принял целиком, граница выше."
        )

    refusals = plural(throttle_events, "отказ", "отказа", "отказов")
    return (
        f"Темп ≈{per_minute} {requests} в минуту, при этом {throttle_events} "
        f"{refusals}. Граница проходит вплотную к этому значению: клиент "
        "нащупал её и отступил, до блокировки не дошло."
    )


@dataclass(frozen=True, slots=True)
class ProgressViewModel:
    started_at: str
    names_discovered: int
    files_with_content: int
    percent: int
    status_label: str
    status_code: str
    is_running: bool
    pause_note: str | None
    error: str | None
    hint: str | None
    pacing: PacingViewModel | None

    @classmethod
    def build(cls, progress: DownloadProgress, moment: MomentFormatter) -> Self:
        run = progress.run
        if run is None:
            return cls(
                started_at=moment(None),
                names_discovered=0,
                files_with_content=0,
                percent=0,
                status_label="не запускался",
                status_code="idle",
                is_running=False,
                pause_note=None,
                error=None,
                hint=None,
                pacing=None,
            )

        pause_note = None
        if progress.pause_remaining_seconds is not None:
            pause_note = (
                f"Пауза ещё {progress.pause_remaining_seconds} с — {run.pause_message}. "
                "Это штатное поведение: каталог ограничивает частоту запросов, "
                "клиент ждёт ровно столько, сколько указано в Retry-After."
            )

        return cls(
            started_at=moment(run.started_at),
            names_discovered=progress.names_discovered,
            files_with_content=progress.files_with_content,
            percent=progress.percent,
            status_label=_STATUS_LABELS[run.status],
            status_code=run.status.value,
            is_running=progress.is_running,
            pause_note=pause_note,
            error=run.error,
            hint=hint_for(run.status, progress.files_with_content),
            pacing=PacingViewModel.build(run),
        )


#: Столбцы, по которым разрешена сортировка. Один список на оба места, где
#: показываются файлы, — иначе таблицы разъедутся по возможностям.
SORTABLE_COLUMNS: tuple[tuple[str, SortField], ...] = (
    ("Имя файла", SortField.name),
    ("Время скачивания (НСК)", SortField.downloaded_at),
)


@dataclass(frozen=True, slots=True)
class SortableColumnViewModel:
    """Состояние сортировки для одного заголовка таблицы.

    Стрелка рисуется только у активного столбца, подсказка описывает результат
    нажатия. Всё уже посчитано — шаблону остаётся отрисовать.
    """

    title: str
    arrow: str
    aria: str
    hint: str
    url: str

    @classmethod
    def build(
        cls,
        title: str,
        sorting: Sorting,
        field: SortField,
        url_for: Callable[[Sorting], str],
    ) -> Self:
        return cls(
            title=title,
            arrow=sorting.arrow(field),
            aria=sorting.aria_value(field),
            hint=sorting.hint(field),
            # Нажатие всегда возвращает на первую страницу: остаться на
            # седьмой при новом порядке — значит показать случайный кусок.
            url=url_for(sorting.toggled(field)),
        )


@dataclass(frozen=True, slots=True)
class FileRowViewModel:
    name: str
    downloaded_at: str


@dataclass(frozen=True, slots=True)
class FileListViewModel:
    rows: tuple[FileRowViewModel, ...]
    total: int
    number: int
    pages: int
    scope_value: str
    everything_selected: bool
    columns: tuple[SortableColumnViewModel, ...]
    previous_url: str | None
    next_url: str | None

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @classmethod
    def build(
        cls,
        page: FilePage,
        sorting: Sorting,
        scope: SelectionScope,
        moment: MomentFormatter,
    ) -> Self:
        # Ссылки собираются здесь целиком, вместе с режимом выбора: переход по
        # страницам — навигация, и всё, что должно его пережить, обязано быть
        # в адресе.
        def url(number: int, order: Sorting) -> str:
            return (
                f"/files?page={number}"
                f"&sort={order.field.value}&dir={order.direction.value}"
                f"&scope={scope.value}"
            )

        return cls(
            rows=tuple(
                FileRowViewModel(name=str(record.name), downloaded_at=moment(record.downloaded_at))
                for record in page.records
            ),
            total=page.total,
            number=page.number,
            pages=page.pages,
            scope_value=scope.value,
            everything_selected=scope.is_everything,
            columns=tuple(
                SortableColumnViewModel.build(title, sorting, field, lambda order: url(1, order))
                for title, field in SORTABLE_COLUMNS
            ),
            previous_url=url(page.number - 1, sorting) if page.has_previous else None,
            next_url=url(page.number + 1, sorting) if page.has_next else None,
        )


@dataclass(frozen=True, slots=True)
class DigitCellViewModel:
    digit: str
    count: int
    share: str


@dataclass(frozen=True, slots=True)
class FileDigitsViewModel:
    number: int
    name: str
    downloaded_at: str
    counts: tuple[int, ...]
    total: int


@dataclass(frozen=True, slots=True)
class StatisticsViewModel:
    digits: tuple[str, ...]
    totals: tuple[DigitCellViewModel, ...]
    grand_total: int
    per_file: tuple[FileDigitsViewModel, ...]
    files_selected: int
    page: int
    pages: int
    previous_url: str | None
    next_url: str | None
    columns: tuple[SortableColumnViewModel, ...]

    @classmethod
    def build(cls, statistics: CatalogStatistics, moment: MomentFormatter) -> Self:
        sorting = statistics.sorting

        # Все адреса собираются здесь, а не по кускам в разметке: там литеральные
        # «&» не экранируются, а подставленные значения — экранируются, и две
        # соседние таблицы начинают отдавать ссылки в разных форматах.
        def url(page: int, order: Sorting) -> str:
            return f"/stats?page={page}&sort={order.field.value}&dir={order.direction.value}"

        return cls(
            digits=DIGITS,
            totals=tuple(_cells(statistics.totals)),
            grand_total=statistics.totals.total,
            per_file=tuple(
                FileDigitsViewModel(
                    number=statistics.first_row_number + offset,
                    name=str(item.name),
                    downloaded_at=moment(item.downloaded_at),
                    counts=tuple(item.counts[digit] for digit in DIGITS),
                    total=item.counts.total,
                )
                for offset, item in enumerate(statistics.per_file)
            ),
            files_selected=statistics.files_selected,
            page=statistics.page,
            pages=statistics.pages,
            previous_url=(url(statistics.page - 1, sorting) if statistics.has_previous else None),
            next_url=url(statistics.page + 1, sorting) if statistics.has_next else None,
            columns=tuple(
                SortableColumnViewModel.build(title, sorting, field, lambda order: url(1, order))
                for title, field in SORTABLE_COLUMNS
            ),
        )


def _cells(counts: DigitCounts) -> list[DigitCellViewModel]:
    return [
        DigitCellViewModel(digit=digit, count=counts[digit], share=percent(counts.share(digit)))
        for digit in DIGITS
    ]
