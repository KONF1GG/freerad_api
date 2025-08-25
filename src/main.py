"""
Основной модуль Radius Core.

Содержит только импорты и минимальную логику запуска.
Весь функционал вынесен в отдельные модули.
"""

from radius_core.core import create_app

app = create_app()

__all__ = ["app"]
