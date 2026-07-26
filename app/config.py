"""Настройки приложения.

Сгруппированы по назначению, а не свалены одним плоским списком: у клиента
каталога, подбора темпа и веб-слоя разные зоны ответственности, и держать их
вперемешку — значит не понимать, что на что влияет.

Ограничения, которые диктует внешнее API (не более трёх файлов за запрос,
получасовой бан), сюда не входят: их нельзя настроить, поэтому они живут в
домене как константы — см. ``app/domain/values.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CatalogApiSettings(BaseModel):
    """Доступ к внешнему API каталога."""

    base_url: str = "http://91.199.149.128:18001"
    #: Привязывает прогресс к нам, а не к IP: переезд сервера не обнулит
    #: каталог и не заведёт второй независимый прогресс. Частота запросов
    #: считается по IP, сменой идентификатора её не обойти.
    #:
    #: Значение обязано быть уникальным: каталог отдаёт каждый файл ровно один
    #: раз на идентификатор, и с уже использованным ручка имён сразу вернёт
    #: пустой список. Значение по умолчанию годится только для локальной
    #: пробы — в ``.env`` его нужно заменить.
    candidate_id: str = "filestats-local"
    timeout_seconds: float = Field(default=30.0, gt=0)
    network_retries: int = Field(default=4, ge=0)


class PacingSettings(BaseModel):
    """Подбор темпа запросов.

    API не публикует свои лимиты и не отдаёт заголовков ``X-RateLimit-*``,
    поэтому темп нащупывается на ходу. Подстройка намеренно асимметрична:
    цена лишнего запроса — получасовой бан, цена лишней паузы — секунды.
    """

    initial_interval_seconds: float = Field(default=1.0, gt=0)
    min_interval_seconds: float = Field(default=0.35, gt=0)
    max_interval_seconds: float = Field(default=30.0, gt=0)
    #: Сколько успехов подряд нужно, чтобы позволить себе ускориться.
    speedup_after_successes: int = Field(default=12, ge=1)
    speedup_factor: float = Field(default=0.85, gt=0, lt=1)
    backoff_factor: float = Field(default=2.0, gt=1)
    #: Запас поверх Retry-After: сервер округляет секунды вниз, и без запаса
    #: первый же запрос после паузы снова попадает в лимит.
    retry_after_padding_seconds: float = Field(default=0.5, ge=0)
    #: Потолок ожидания, если Retry-After отсутствует или абсурдно велик.
    max_sleep_seconds: float = Field(default=2400.0, gt=0)
    #: Сколько подряд идущих отказов терпим, прежде чем признать прогон
    #: неуспешным. Без предела процесс молча висит в паузах сутками.
    max_consecutive_throttles: int = Field(default=20, ge=1)


class DownloadSettings(BaseModel):
    """Поведение обхода каталога."""

    #: Скольким файлам даём накопиться перед отметкой. Ручка отметки не
    #: ограничивает количество имён, поэтому одним запросом закрываем сразу
    #: несколько троек. Слишком большая пачка невыгодна: неотмеченные имена
    #: продолжают возвращаться в выдаче и тратят запросы впустую.
    confirm_batch_size: int = Field(default=30, ge=1)


class WebSettings(BaseModel):
    """Представление."""

    page_size: int = Field(default=50, ge=1, le=500)
    display_timezone: str = "Asia/Novosibirsk"


class LoggingSettings(BaseModel):
    """Логи.

    JSON по умолчанию: логи остаются пригодными и для `jq`, и для любого
    сборщика, если он когда-нибудь понадобится. ``text`` — для локальной
    разработки, где важнее читаемость глазами.
    """

    level: str = "INFO"
    format: str = Field(default="json", pattern="^(json|text)$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://filestats:filestats@db:5432/filestats"

    catalog_api: CatalogApiSettings = Field(default_factory=CatalogApiSettings)
    pacing: PacingSettings = Field(default_factory=PacingSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


settings = Settings()
