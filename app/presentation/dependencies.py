"""Зависимости слоя представления.

Вынесено из модуля маршрутов, потому что контейнер нужен и страницам, и API:
импортировать один набор маршрутов из другого ради общей функции значило бы
связать их между собой без всякой причины.
"""

from __future__ import annotations

from fastapi import Request

from app.container import Container


def get_container(request: Request) -> Container:
    """Контейнер живёт на приложении и собирается один раз при старте."""
    return request.app.state.container
