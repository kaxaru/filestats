"""Реализации портов совпадают с объявленными протоколами.

Protocol в Python не проверяется ни при импорте, ни в рантайме: порт может
годами объявлять одну сигнатуру, а реализация принимать другую, и никто не
заметит. Такое уже случалось — ``CatalogQueries.per_file_digits`` объявлял
контракт без параметра сортировки, хотя и реализация, и вызывающий код его
передавали.

Сравниваются имена, вид и наличие умолчания у параметров — не аннотации:
реализация вправе уточнять типы, но не менять набор аргументов.
"""

from __future__ import annotations

import inspect

import pytest

from app.application.download_service import RunPauseObserver
from app.application.ports import (
    CatalogClient,
    CatalogQueries,
    FileRepository,
    RunRepository,
    ThrottleObserver,
)
from app.infrastructure.catalog_api.client import HttpCatalogClient
from app.infrastructure.persistence.queries import SqlAlchemyCatalogQueries
from app.infrastructure.persistence.repositories import (
    SqlAlchemyFileRepository,
    SqlAlchemyRunRepository,
)
from tests.fakes import FakeCatalogClient, InMemoryFileRepository, InMemoryRunRepository

#: Каждый порт и всё, что за него выдаёт себя, — включая подставные реализации
#: из тестов: разошедшаяся заглушка обманывает проверки так же успешно.
PORT_IMPLEMENTATIONS = [
    (CatalogClient, HttpCatalogClient),
    (CatalogClient, FakeCatalogClient),
    (FileRepository, SqlAlchemyFileRepository),
    (FileRepository, InMemoryFileRepository),
    (RunRepository, SqlAlchemyRunRepository),
    (RunRepository, InMemoryRunRepository),
    (CatalogQueries, SqlAlchemyCatalogQueries),
    (ThrottleObserver, RunPauseObserver),
]


def _contract(method) -> list[tuple[str, inspect._ParameterKind, bool]]:
    """Набор параметров без self и без аннотаций."""
    parameters = list(inspect.signature(method).parameters.values())
    return [
        (parameter.name, parameter.kind, parameter.default is not inspect.Parameter.empty)
        for parameter in parameters
        if parameter.name != "self"
    ]


def _declared_methods(port: type) -> list[str]:
    return [
        name for name, member in vars(port).items() if callable(member) and not name.startswith("_")
    ]


# Каждый метод — отдельный случай, а не итерация внутри одного теста:
# расхождение в первом методе не должно скрывать расхождения в остальных.
PORT_METHODS = [
    (port, implementation, method)
    for port, implementation in PORT_IMPLEMENTATIONS
    for method in _declared_methods(port)
]


@pytest.mark.parametrize(
    ("port", "implementation", "method"),
    PORT_METHODS,
    ids=[f"{port.__name__}.{method}-{impl.__name__}" for port, impl, method in PORT_METHODS],
)
def test_implementation_matches_port(port: type, implementation: type, method: str) -> None:
    actual = getattr(implementation, method, None)
    assert actual is not None, f"{implementation.__name__} не реализует {method}"

    expected_contract = _contract(getattr(port, method))
    actual_contract = _contract(actual)
    assert actual_contract == expected_contract, (
        f"{implementation.__name__}.{method} расходится с портом "
        f"{port.__name__}.{method}: ожидалось {expected_contract}, "
        f"получено {actual_contract}"
    )


def test_every_port_declares_methods() -> None:
    """Иначе параметризация оказалась бы пустой и проверка — бесполезной."""
    without_methods = [
        port.__name__ for port, _ in PORT_IMPLEMENTATIONS if not _declared_methods(port)
    ]
    assert without_methods == []
