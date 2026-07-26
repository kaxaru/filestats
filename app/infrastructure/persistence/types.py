"""Типы SQLAlchemy для объектов-значений домена.

Позволяют хранить ``FileName`` и ``FileContent`` как есть, не разворачивая их
в примитивы на каждом обращении к хранилищу. Проверки объекта-значения при
этом срабатывают и на чтении из базы.
"""

from __future__ import annotations

from sqlalchemy import String, Text, TypeDecorator

from app.domain.values import FileContent, FileName

FILE_NAME_MAX_LENGTH = 128


class FileNameType(TypeDecorator):
    """``FileName`` ↔ ``VARCHAR``."""

    impl = String(FILE_NAME_MAX_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: FileName | str | None, dialect) -> str | None:
        if value is None:
            return None
        return value.value if isinstance(value, FileName) else str(value)

    def process_result_value(self, value: str | None, dialect) -> FileName | None:
        return None if value is None else FileName(value)


class FileContentType(TypeDecorator):
    """``FileContent`` ↔ ``TEXT``."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: FileContent | str | None, dialect) -> str | None:
        if value is None:
            return None
        return value.raw if isinstance(value, FileContent) else str(value)

    def process_result_value(self, value: str | None, dialect) -> FileContent | None:
        return None if value is None else FileContent(value)
