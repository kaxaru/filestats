"""Настройка логирования.

Формат по умолчанию — JSON: логи читаются `jq`, а при необходимости
подхватываются любым сборщиком без переписывания приложения. Разворачивать
ради одного сервиса на одном хосте отдельный стек сбора логов смысла нет,
но и закрывать себе эту дорогу текстовым форматом тоже незачем.

Для локальной разработки есть человекочитаемый вариант — ``LOGGING__FORMAT=text``.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: [%(request_id)s] %(message)s"

#: Идентификатор текущего запроса. Через contextvar, а не через параметр:
#: иначе его пришлось бы протаскивать сквозь все слои до самого домена ради
#: одной строчки в логе.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Проставляет идентификатор запроса каждой записи.

    Без него структурные логи бесполезны: сообщения от разных запросов
    вперемешку не связать между собой, а именно связность и есть причина
    заводить JSON вместо текста.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


#: Служебные поля LogRecord — всё, чего здесь нет, считается полезной нагрузкой
#: вызывающего кода и попадает в лог как отдельное поле.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Одна строка JSON на запись."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Всё, что передали через extra=, сохраняем как поля верхнего уровня —
        # ради этого структурные логи и заводятся.
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED and not key.startswith("_")
            }
        )

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(level: str = "INFO", output_format: str = "json") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if output_format == "json" else logging.Formatter(TEXT_FORMAT)
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn настраивает своё логирование сам и вешает собственные обработчики.
    # Если их не снять, часть строк пойдёт мимо нашего формата, и поток логов
    # перестанет быть однородным — а именно однородность делает его пригодным
    # для машинной обработки.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Свой access-лог пишет middleware — структурно и с идентификатором
    # запроса. Штатный uvicorn дублировал бы его в другом формате.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx рассказывает про каждый запрос к каталогу на INFO; при обходе в
    # тысячи файлов это шум, который прячет собственные события сервиса.
    logging.getLogger("httpx").setLevel(logging.WARNING)
