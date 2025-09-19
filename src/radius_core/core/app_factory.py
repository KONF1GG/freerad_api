"""
Фабрика для создания FastAPI приложения Radius Core.

Включает:
- Создание экземпляра FastAPI
- Настройку middleware
- Настройку Prometheus инструментации
- Подключение роутера с эндпоинтами
"""

import asyncio
import os

import uvloop
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# from prometheus_fastapi_instrumentator import Instrumentator

from .app_lifecycle import lifecycle_manager
from ..api import router


def create_app() -> FastAPI:
    """Создание и настройка FastAPI приложения."""

    # Создаем директорию для Prometheus multiprocess метрик
    prometheus_dir = os.getenv(
        "PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_multiproc_dir"
    )
    os.makedirs(prometheus_dir, exist_ok=True)
    # Не перезаписываем переменную, если она уже установлена
    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = prometheus_dir

    # Установка политики событийного цикла для ускорения
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    # Создание приложения с lifespan
    app = FastAPI(
        title="Async Radius Microservice",
        description="Asynchronous RADIUS service",
        version="1.0.0",
        lifespan=lifecycle_manager.lifespan,
    )


    # Настройка CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Подключаем роутер с эндпоинтами
    app.include_router(router)

    return app


# Глобальный экземпляр приложения
_radius_app = create_app()
