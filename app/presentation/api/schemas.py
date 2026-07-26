"""Схемы REST API.

Отдельный слой между доменом и проводом. Доменные объекты наружу не отдаются:
их форма подчинена инвариантам, а не удобству клиента, и любое их изменение
иначе ломало бы контракт API. Заодно эти же классы порождают OpenAPI — то есть
документация не расходится с кодом по построению.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field

from app.application.ports import FileDetails, FileDigits, FilePage
from app.application.progress_service import DownloadProgress
from app.application.stats_service import CatalogStatistics
from app.domain.digits import DIGITS, DigitCounts
from app.domain.file import CatalogFile
from app.domain.run import DownloadRun

DIGIT_EXAMPLE = {digit: 50 for digit in DIGITS}


class DigitCountsSchema(BaseModel):
    """Сколько раз встретилась каждая цифра.

    Отдаётся отображением «цифра → количество», а не десятью полями ``d0..d9``:
    в хранилище колонки нужны ради ``SUM`` на стороне базы, но клиенту удобнее
    словарь, по которому можно пройти циклом.
    """

    counts: dict[str, int] = Field(examples=[DIGIT_EXAMPLE])
    total: int = Field(description="Сумма по всем цифрам", examples=[500])

    @classmethod
    def of(cls, counts: DigitCounts) -> Self:
        return cls(counts=counts.as_dict(), total=counts.total)


class FileSchema(BaseModel):
    """Файл в списке."""

    name: str = Field(examples=["000d7d0a-acef-4c95-b92d-1aa496b1858a.txt"])
    downloaded_at: datetime | None = Field(
        description="Момент скачивания в UTC; часовой пояс — забота клиента"
    )
    digits: DigitCountsSchema

    @classmethod
    def of(cls, record: CatalogFile) -> Self:
        return cls(
            name=str(record.name),
            downloaded_at=record.downloaded_at,
            digits=DigitCountsSchema.of(record.counts),
        )


class FileDetailsSchema(FileSchema):
    """Файл целиком, вместе с содержимым."""

    content: str = Field(
        description="Строка цифр, по условию каталога — 500 символов",
        examples=["2395778969177858327525074678250561522341…"],
    )

    @classmethod
    def of_details(cls, details: FileDetails) -> Self:
        return cls(
            name=str(details.name),
            downloaded_at=details.downloaded_at,
            digits=DigitCountsSchema.of(details.counts),
            content=details.content.raw,
        )


class PageSchema(BaseModel):
    """Постраничная выдача."""

    items: list[FileSchema]
    page: int = Field(examples=[1])
    pages: int = Field(examples=[25])
    total: int = Field(description="Всего файлов в выборке", examples=[1234])

    @classmethod
    def of(cls, page: FilePage) -> Self:
        return cls(
            items=[FileSchema.of(record) for record in page.records],
            page=page.number,
            pages=page.pages,
            total=page.total,
        )


class SelectionSchema(BaseModel):
    """Какие файлы участвуют в расчёте.

    «Весь каталог» — отдельный режим, а не перечисление всех имён: на стороне
    хранилища это отсутствие условия, и гонять тысячи строк по сети незачем.
    """

    scope: str = Field(
        default="chosen",
        pattern="^(chosen|everything)$",
        description="`chosen` — по перечисленным именам, `everything` — по всему каталогу",
        examples=["everything"],
    )
    names: list[str] = Field(
        default_factory=list,
        description="Имена файлов; учитываются только при `scope=chosen`",
        examples=[["000d7d0a-acef-4c95-b92d-1aa496b1858a.txt"]],
    )


class FileDigitsSchema(BaseModel):
    """Строка разбивки по файлам."""

    name: str
    downloaded_at: datetime | None
    digits: DigitCountsSchema

    @classmethod
    def of(cls, item: FileDigits) -> Self:
        return cls(
            name=str(item.name),
            downloaded_at=item.downloaded_at,
            digits=DigitCountsSchema.of(item.counts),
        )


class StatisticsSchema(BaseModel):
    """Результат расчёта.

    Общий итог посчитан по всей выборке независимо от её размера, разбивка по
    файлам выдаётся постранично.
    """

    totals: DigitCountsSchema = Field(description="Итог по всей выборке")
    files_selected: int = Field(description="Сколько файлов участвовало", examples=[1234])
    per_file: list[FileDigitsSchema]
    page: int
    pages: int

    @classmethod
    def of(cls, statistics: CatalogStatistics) -> Self:
        return cls(
            totals=DigitCountsSchema.of(statistics.totals),
            files_selected=statistics.files_selected,
            per_file=[FileDigitsSchema.of(item) for item in statistics.per_file],
            page=statistics.page,
            pages=statistics.pages,
        )


class PacingSchema(BaseModel):
    """Замеры темпа обращения к каталогу.

    Наблюдавшиеся, а не заявленные: каталог свои лимиты не публикует, поэтому
    единственный способ о них судить — измерять собственный трафик.
    """

    requests_made: int = Field(examples=[763])
    throttle_events: int = Field(description="Сколько раз получили 429 или 403", examples=[12])
    seconds_paused: float = Field(examples=[12.0])
    interval_seconds: float | None = Field(
        description="Текущий интервал между запросами", examples=[0.61]
    )
    requests_per_minute: float | None = Field(examples=[70.5])


class RunSchema(BaseModel):
    """Состояние прогона скачивания."""

    id: int | None
    status: str = Field(
        description="`running`, `completed`, `failed` или `stopped`", examples=["completed"]
    )
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    paused_until: datetime | None = Field(
        description="До какого момента клиент ждёт из-за лимита частоты"
    )
    pause_reason: str | None
    pacing: PacingSchema

    @classmethod
    def of(cls, run: DownloadRun) -> Self:
        return cls(
            id=run.id,
            status=run.status.value,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error=run.error,
            paused_until=run.paused_until,
            pause_reason=run.pause_reason.value if run.pause_reason else None,
            pacing=PacingSchema(
                requests_made=run.requests_made,
                throttle_events=run.throttle_events,
                seconds_paused=run.seconds_paused,
                interval_seconds=run.interval_seconds,
                requests_per_minute=run.requests_per_minute,
            ),
        )


class ProgressSchema(BaseModel):
    """Ход обхода каталога.

    ``names_discovered`` растёт по ходу работы: общее число файлов каталог не
    сообщает, поэтому знаменатель прогресса — число уже увиденных имён, а не
    размер каталога.
    """

    run: RunSchema | None
    names_discovered: int = Field(examples=[1234])
    files_downloaded: int = Field(examples=[1234])
    percent: int = Field(ge=0, le=100, examples=[100])
    is_running: bool

    @classmethod
    def of(cls, progress: DownloadProgress) -> Self:
        return cls(
            run=RunSchema.of(progress.run) if progress.run else None,
            names_discovered=progress.names_discovered,
            files_downloaded=progress.files_with_content,
            percent=progress.percent,
            is_running=progress.is_running,
        )


class ErrorSchema(BaseModel):
    """Формат ошибки — тот же, что у FastAPI по умолчанию."""

    detail: str = Field(examples=["Файл не найден"])
