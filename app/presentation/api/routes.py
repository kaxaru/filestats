"""REST API.

Маршруты такие же тонкие, как у HTML-страниц, и обращаются к тем же сценариям:
API — второе представление одних и тех же данных, а не второй набор правил.
Если бы логика жила в контроллерах, её пришлось бы дублировать здесь.

Версия в пути (`/api/v1`) — чтобы несовместимое изменение контракта можно было
выкатить, не ломая существующих клиентов.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.container import Container
from app.domain.selection import Selection
from app.domain.sorting import Sorting
from app.domain.values import FileName, InvalidFileName
from app.presentation.api.schemas import (
    ErrorSchema,
    FileDetailsSchema,
    PageSchema,
    ProgressSchema,
    RunSchema,
    SelectionSchema,
    StatisticsSchema,
)
from app.presentation.dependencies import get_container

router = APIRouter(prefix="/api/v1")

NOT_FOUND = {HTTPStatus.NOT_FOUND.value: {"model": ErrorSchema, "description": "Файл не найден"}}


@router.get(
    "/files",
    tags=["Файлы"],
    summary="Список скачанных файлов",
    response_model=PageSchema,
)
async def list_files(
    page: int = Query(default=1, ge=1, description="Номер страницы, с единицы"),
    size: int = Query(default=50, ge=1, le=500, description="Размер страницы"),
    sort: str | None = Query(default=None, description="`downloaded_at` или `name`"),
    direction: str | None = Query(default=None, alias="dir", description="`asc` или `desc`"),
    container: Container = Depends(get_container),
) -> PageSchema:
    """Постранично, с сортировкой по времени скачивания или по имени.

    Непонятные значения сортировки не приводят к ошибке: страница важнее
    придирки к параметру, поэтому берётся значение по умолчанию.
    """
    file_page = await container.queries.page(
        number=page, size=size, sorting=Sorting.parse(sort, direction)
    )
    return PageSchema.of(file_page)


@router.get(
    "/files/{name}",
    tags=["Файлы"],
    summary="Файл с содержимым",
    response_model=FileDetailsSchema,
    responses=NOT_FOUND,
)
async def get_file(
    name: str,
    container: Container = Depends(get_container),
) -> FileDetailsSchema:
    """Один файл: счётчики цифр и сама строка.

    Недопустимое имя даёт 404, а не 422: с точки зрения клиента и то и другое
    означает «такого файла нет», и различать их незачем.
    """
    try:
        file_name = FileName.parse(name)
    except InvalidFileName as exc:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Файл не найден") from exc

    details = await container.queries.file_details(file_name)
    if details is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Файл не найден")
    return FileDetailsSchema.of_details(details)


@router.post(
    "/stats",
    tags=["Расчёты"],
    summary="Статистика по цифрам",
    response_model=StatisticsSchema,
)
async def compute_stats(
    selection: SelectionSchema = Body(default_factory=SelectionSchema),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    sort: str | None = Query(default=None),
    direction: str | None = Query(default=None, alias="dir"),
    container: Container = Depends(get_container),
) -> StatisticsSchema:
    """Общая статистика по всей выборке и постраничная разбивка по файлам.

    Метод POST при том, что операция читающая: выбор может состоять из тысяч
    имён, а такой список не помещается в строку запроса.
    """
    chosen = (
        Selection.everything() if selection.scope == "everything" else Selection.of(selection.names)
    )
    statistics = await container.statistics_of(size).compute(
        chosen, page=page, sorting=Sorting.parse(sort, direction)
    )
    return StatisticsSchema.of(statistics)


@router.get(
    "/runs/latest",
    tags=["Скачивание"],
    summary="Ход последнего обхода",
    response_model=ProgressSchema,
)
async def latest_run(container: Container = Depends(get_container)) -> ProgressSchema:
    """Состояние обхода и показатели прогресса.

    Если обход ни разу не запускали, `run` будет `null`, а счётчики нулевыми.
    """
    return ProgressSchema.of(await container.progress.snapshot())


@router.post(
    "/runs",
    tags=["Скачивание"],
    summary="Запустить обход каталога",
    response_model=RunSchema,
    status_code=HTTPStatus.ACCEPTED.value,
)
async def start_run(container: Container = Depends(get_container)) -> RunSchema:
    """Запускает фоновый обход и сразу возвращает прогон.

    Операция идемпотентна: если обход уже идёт, вернётся он же, а второй
    параллельный не запустится. Это не удобство, а необходимость — лимит
    частоты считается по адресу клиента, и два обхода лишь ускорили бы
    блокировку.

    Отвечает `202`, а не `201`: работа принята, но к моменту ответа ещё не
    выполнена.
    """
    return RunSchema.of(await container.begin_download())
