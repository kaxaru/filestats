"""HTTP-клиент каталога — реализация порта ``CatalogClient``.

Отказы по частоте запросов обрабатываются внутри: клиент ждёт положенное время
и повторяет запрос сам. Наружу они не протекают, иначе каждый вызывающий был бы
обязан повторять одну и ту же логику ретраев. О паузах можно узнать через
``ThrottleObserver`` — чтобы ожидание было видно в интерфейсе, а не выглядело
зависанием.
"""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus

import httpx

from app.application.errors import CatalogThrottled, CatalogUnavailable, FileMissingFromCatalog
from app.application.ports import MarkOutcome, PacingSnapshot
from app.config import CatalogApiSettings, PacingSettings
from app.domain.pausing import Pause, PauseReason
from app.domain.values import (
    BAN_DURATION_SECONDS,
    MAX_FILES_PER_DOWNLOAD,
    FileContent,
    FileName,
)
from app.infrastructure.catalog_api.archive import unpack
from app.infrastructure.catalog_api.pacing import AdaptivePacer, parse_retry_after

logger = logging.getLogger(__name__)

NAMES_PATH = "/api/files/names"
DOWNLOAD_PATH = "/api/files/download"
CONFIRM_PATH = "/api/files/downloaded"

CANDIDATE_HEADER = "X-Candidate-Id"
RETRY_AFTER_HEADER = "Retry-After"

#: Сколько ждать при 429, если сервер не прислал Retry-After.
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 60.0

#: Отступ при сетевых сбоях: 2, 4, 8… но не дольше потолка.
NETWORK_BACKOFF_BASE = 2
NETWORK_BACKOFF_CAP_SECONDS = 30

THROTTLING_STATUSES = frozenset({HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.FORBIDDEN})

_PAUSE_REASONS = {
    HTTPStatus.TOO_MANY_REQUESTS: PauseReason.rate_limited,
    HTTPStatus.FORBIDDEN: PauseReason.banned,
}

_DEFAULT_WAITS = {
    HTTPStatus.TOO_MANY_REQUESTS: DEFAULT_RATE_LIMIT_WAIT_SECONDS,
    HTTPStatus.FORBIDDEN: float(BAN_DURATION_SECONDS),
}


class HttpCatalogClient:
    """Клиент трёх ручек каталога."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_settings: CatalogApiSettings,
        pacing_settings: PacingSettings,
        observer=None,
    ) -> None:
        self._http = http
        self._api = api_settings
        self._pacing = pacing_settings
        self._pacer = AdaptivePacer(pacing_settings)
        self._observer = observer
        self._requests_made = 0
        self._throttle_events = 0
        self._seconds_paused = 0.0

    def pacing(self) -> PacingSnapshot:
        return PacingSnapshot(
            requests_made=self._requests_made,
            throttle_events=self._throttle_events,
            seconds_paused=self._seconds_paused,
            interval_seconds=self._pacer.interval,
        )

    async def fetch_names(self) -> list[FileName]:
        response = await self._request("GET", NAMES_PATH)
        return [FileName.parse(name) for name in response.json()["file_names"]]

    async def fetch_contents(self, names: list[FileName]) -> dict[FileName, FileContent]:
        """Скачать файлы.

        При ``404`` каталог отвергает всю тройку целиком, даже если отсутствует
        одно имя. Поэтому пачка пересобирается поштучно: два исправных файла не
        должны пропадать из-за одного битого.
        """
        if not names:
            return {}
        if len(names) > MAX_FILES_PER_DOWNLOAD:
            raise ValueError(f"за один запрос каталог отдаёт не более {MAX_FILES_PER_DOWNLOAD}")

        payload = {"file_names": [name.value for name in names]}
        try:
            response = await self._request("POST", DOWNLOAD_PATH, json=payload)
        except FileMissingFromCatalog:
            if len(names) == 1:
                raise
            return await self._fetch_one_by_one(names)

        return unpack(response.content)

    async def confirm_downloaded(self, names: list[FileName]) -> MarkOutcome:
        """Отметить файлы скачанными.

        Ограничения на количество имён у этой ручки нет — в отличие от
        скачивания. Операция идемпотентна, поэтому повтор после сбоя безопасен.
        """
        if not names:
            return MarkOutcome(marked_now=0, already_marked=0)

        response = await self._request(
            "POST", CONFIRM_PATH, json={"file_names": [name.value for name in names]}
        )
        body = response.json()
        return MarkOutcome(marked_now=body["marked_now"], already_marked=body["already_marked"])

    async def _fetch_one_by_one(self, names: list[FileName]) -> dict[FileName, FileContent]:
        logger.warning("404 на пачке %s — пересобираю поштучно", [str(n) for n in names])
        collected: dict[FileName, FileContent] = {}
        for name in names:
            try:
                collected.update(await self.fetch_contents([name]))
            except FileMissingFromCatalog:
                logger.error("файла %s нет в каталоге, пропускаю", name)
        return collected

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {CANDIDATE_HEADER: self._api.candidate_id}
        network_attempt = 0
        throttle_streak = 0

        while True:
            await self._pacer.acquire()

            try:
                self._requests_made += 1
                response = await self._http.request(method, path, headers=headers, **kwargs)
            except (httpx.TransportError, httpx.StreamError) as exc:
                network_attempt += 1
                if network_attempt > self._api.network_retries:
                    raise CatalogUnavailable(
                        f"сеть недоступна после {network_attempt} попыток: {exc}"
                    ) from exc
                pause = Pause(
                    reason=PauseReason.network_error,
                    seconds=min(NETWORK_BACKOFF_BASE**network_attempt, NETWORK_BACKOFF_CAP_SECONDS),
                    detail=str(exc),
                )
                self._seconds_paused += pause.seconds
                await self._wait(pause)
                continue

            network_attempt = 0
            status = HTTPStatus(response.status_code)

            if status in THROTTLING_STATUSES:
                wait_seconds = min(
                    parse_retry_after(
                        response.headers.get(RETRY_AFTER_HEADER),
                        fallback=_DEFAULT_WAITS[status],
                    ),
                    self._pacing.max_sleep_seconds,
                )
                self._pacer.on_throttled(wait_seconds)
                self._throttle_events += 1
                self._seconds_paused += wait_seconds

                throttle_streak += 1
                if throttle_streak > self._pacing.max_consecutive_throttles:
                    raise CatalogThrottled(wait_seconds)

                logger.warning(
                    "%s на %s: пауза %.1f с, интервал теперь %.2f с",
                    status.value,
                    path,
                    wait_seconds,
                    self._pacer.interval,
                )
                await self._notify(Pause(_PAUSE_REASONS[status], wait_seconds))
                continue

            if status is HTTPStatus.NOT_FOUND:
                raise FileMissingFromCatalog(kwargs.get("json", {}).get("file_names", []))

            response.raise_for_status()
            throttle_streak = 0
            self._pacer.on_success()
            return response

    async def _wait(self, pause: Pause) -> None:
        logger.warning("%s, повтор через %.0f с", pause.message, pause.seconds)
        await self._notify(pause)
        await asyncio.sleep(pause.seconds)

    async def _notify(self, pause: Pause) -> None:
        if self._observer is not None:
            await self._observer.on_wait(pause)
