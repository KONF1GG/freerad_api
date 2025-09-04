"""
Основной модуль Radius Core.

Содержит только импорты и минимальную логику запуска.
Весь функционал вынесен в отдельные модули.
"""
import uvicorn
from radius_core.core import create_app

app = create_app()

uvicorn.run(app, host="0.0.0.0", port=8000)

__all__ = ["app"]
