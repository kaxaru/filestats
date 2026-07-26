"""HTTP-маршруты.

Контроллеры намеренно тонкие: разобрать запрос, вызвать сценарий, собрать
ViewModel. Ни расчётов, ни обращений к хранилищу здесь нет — иначе слой
представления снова станет свалкой, где бизнес-правила перемешаны с разметкой.
"""

from __future__ import annotations

import enum
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.container import Container
from app.domain.sorting import Sorting
from app.presentation.scope import SelectionScope
from app.presentation.viewmodels import (
    FileListViewModel,
    ProgressViewModel,
    StatisticsViewModel,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()

SEE_OTHER = 303


class Page(enum.StrEnum):
    """Активный пункт навигации."""

    download = "download"
    files = "files"


SERVICE_UNAVAILABLE = 503

HX_REQUEST_HEADER = "HX-Request"
HX_HISTORY_RESTORE_HEADER = "HX-History-Restore-Request"


def get_container(request: Request) -> Container:
    return request.app.state.container


@router.get("/health")
async def health(container: Container = Depends(get_container)) -> JSONResponse:
    """Готовность сервиса.

    Проверяет базу настоящим запросом: без неё сервис не может ни скачивать,
    ни считать, поэтому «поднялся процесс» — недостаточный признак живости.
    """
    database_ok = await container.health.is_reachable()
    run = await container.runs.latest() if database_ok else None

    return JSONResponse(
        status_code=200 if database_ok else SERVICE_UNAVAILABLE,
        content={
            "status": "ok" if database_ok else "degraded",
            "database": "ok" if database_ok else "unreachable",
            "download": run.status.value if run else "never_started",
            "worker_running": container.worker.is_running,
        },
    )


@router.get("/", response_class=HTMLResponse)
async def download_page(request: Request, container: Container = Depends(get_container)):
    snapshot = await container.progress.snapshot()
    return templates.TemplateResponse(
        request,
        "download.html",
        {
            "progress": ProgressViewModel.build(snapshot, container.moment),
            "active": Page.download,
        },
    )


@router.post("/download/start")
async def start_download(container: Container = Depends(get_container)):
    await container.begin_download()
    return RedirectResponse("/", status_code=SEE_OTHER)


@router.get("/download/progress", response_class=HTMLResponse)
async def download_progress(request: Request, container: Container = Depends(get_container)):
    """Фрагмент разметки для опроса из браузера — обновляется только блок показателей."""
    snapshot = await container.progress.snapshot()
    return templates.TemplateResponse(
        request,
        "partials/progress.html",
        {"progress": ProgressViewModel.build(snapshot, container.moment)},
    )


@router.get("/files", response_class=HTMLResponse)
async def files_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None),
    scope: SelectionScope = Query(default=SelectionScope.chosen),
    container: Container = Depends(get_container),
):
    """Список скачанных файлов.

    Режим выбора живёт в строке запроса наравне со страницей и сортировкой:
    переход по страницам — навигация, и состояние, которое хранилось бы только
    в DOM, при этом терялось бы.
    """
    sorting = Sorting.parse(sort, dir)
    file_page = await container.queries.page(
        number=page, size=container.settings.web.page_size, sorting=sorting
    )
    return templates.TemplateResponse(
        request,
        _file_list_template(request),
        {
            "files": FileListViewModel.build(file_page, sorting, scope, container.moment),
            "active": Page.files,
        },
    )


def _file_list_template(request: Request) -> str:
    """Фрагмент для листания, целая страница — во всех остальных случаях.

    При восстановлении истории браузером фрагмента недостаточно: страницу надо
    собрать заново целиком, иначе кнопка «назад» покажет голый кусок разметки.
    """
    is_fragment_request = (
        request.headers.get(HX_REQUEST_HEADER) == "true"
        and request.headers.get(HX_HISTORY_RESTORE_HEADER) != "true"
    )
    return "partials/file_list.html" if is_fragment_request else "files.html"


@router.post("/stats", response_class=HTMLResponse)
async def compute_statistics(
    request: Request,
    names: list[str] = Form(default=[]),
    scope: SelectionScope = Form(default=SelectionScope.chosen),
    page: int = Query(default=1, ge=1),
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None),
    container: Container = Depends(get_container),
):
    """Расчёты по выбранным файлам.

    Страница и порядок приходят в строке запроса, а сам выбор — в теле формы:
    переход по страницам разбивки не должен требовать заново отмечать файлы.
    """
    selection = scope.to_selection(names)
    if selection.is_empty:
        return templates.TemplateResponse(
            request,
            "partials/statistics.html",
            {"statistics": None, "message": "Не выбрано ни одного файла."},
        )

    result = await container.statistics.compute(
        selection, page=page, sorting=Sorting.parse(sort, dir)
    )
    return templates.TemplateResponse(
        request,
        "partials/statistics.html",
        {
            "statistics": StatisticsViewModel.build(result, container.moment),
            "message": None,
        },
    )
