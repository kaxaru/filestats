"""Объекты-значения и правила каталога.

Здесь же собраны ограничения, которые диктует внешнее API. Они не настройки:
крутить их бессмысленно, сервер всё равно ответит отказом. Поэтому им место в
домене как константам с именем, а не числам, разбросанным по коду.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from app.domain.digits import DIGITS, DigitCounts
from app.domain.errors import DomainError

#: Больше трёх файлов за один запрос API не отдаёт — «скачать всё разом»
#: закрыто намеренно.
MAX_FILES_PER_DOWNLOAD = 3

#: Штатная длина содержимого по условию задачи: одна строка из 500 цифр.
CANONICAL_CONTENT_LENGTH = 500

#: Срок блокировки за злоупотребление запросами.
BAN_DURATION_SECONDS = 30 * 60

_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidFileName(DomainError):
    """Имя файла не похоже на имя файла."""


@dataclass(frozen=True, slots=True, order=True)
class FileName:
    """Имя файла в каталоге.

    Проверка не косметическая: имена приходят снаружи и используются как ключи
    хранилища и как пути внутри ZIP-архива. Точки-родители и слэши в таком
    имени — это уже обход каталога, а не опечатка.
    """

    value: str

    def __post_init__(self) -> None:
        if not _FILE_NAME.match(self.value) or ".." in self.value:
            raise InvalidFileName(f"недопустимое имя файла: {self.value!r}")

    @classmethod
    def parse(cls, raw: str) -> Self:
        return cls(raw.strip())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FileContent:
    """Содержимое файла — строка цифр.

    Содержимое не отвергается, даже если отличается от ожидаемого формата:
    файл уже скачан, и выбросить его — значит потерять безвозвратно. Вместо
    отказа несоответствие отмечается флагом ``is_canonical``.
    """

    raw: str

    @classmethod
    def parse(cls, raw: str) -> Self:
        return cls(raw.strip())

    @property
    def length(self) -> int:
        return len(self.raw)

    @property
    def is_canonical(self) -> bool:
        return self.length == CANONICAL_CONTENT_LENGTH and all(char in DIGITS for char in self.raw)

    def digit_counts(self) -> DigitCounts:
        return DigitCounts.from_content(self.raw)

    def __str__(self) -> str:
        return self.raw
