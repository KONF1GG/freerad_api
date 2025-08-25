"""
Фабрика для создания FastAPI приложения Radius Core.

Включает:
- Создание экземпляра FastAPI
- Настройку middleware
- Настройку Prometheus инструментации
- Подключение роутера с эндпоинтами
- Оптимизации производительности
"""

import asyncio
import os

import uvloop
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
# from prometheus_fastapi_instrumentator import Instrumentator

from .app_lifecycle import lifecycle_manager
from .logging_config import logger
from .performance_optimizations import performance_optimizer
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

    # Оптимизация asyncio для производительности
    if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        # Для Windows
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # Настройка лимитов для asyncio
    try:
        import resource

        # Увеличиваем лимиты файловых дескрипторов
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        logger.info(f"Установлен лимит файловых дескрипторов: {hard}")
    except (ImportError, OSError):
        pass

    # Создание приложения с lifespan
    app = FastAPI(
        title="Async Radius Microservice",
        description="Asynchronous RADIUS service with performance optimizations",
        version="1.0.0",
        lifespan=lifecycle_manager.lifespan,
        # Оптимизации производительности
        docs_url=None
        if os.getenv("DISABLE_DOCS", "false").lower() == "true"
        else "/docs",
        redoc_url=None
        if os.getenv("DISABLE_DOCS", "false").lower() == "true"
        else "/redoc",
    )

    # instrumentator = Instrumentator(
    #     should_group_status_codes=False,
    #     should_ignore_untemplated=True,
    #     should_group_untemplated=False,
    #     excluded_handlers=["/metrics", "/health", "/"],
    #     should_instrument_requests_inprogress=True,
    #     should_respect_env_var=False,
    #     inprogress_name="inprogress",
    #     inprogress_labels=True,
    # )
    # instrumentator.instrument(app)

    # Настройка CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Добавляем GZip сжатие для оптимизации
    app.add_middleware(
        GZipMiddleware,
        minimum_size=1000,  # Сжимаем только большие ответы
    )

    # Подключаем роутер с эндпоинтами
    app.include_router(router)

    # Добавляем обработчики для оптимизации производительности
    @app.on_event("startup")
    async def startup_optimizations():
        """Оптимизации при запуске приложения"""
        try:
            # Инициализация оптимизатора производительности
            logger.info("Инициализация оптимизаций производительности...")

            # Настройка GC для производительности
            import gc

            gc.set_threshold(700, 10, 10)

            # Прогрев соединений будет выполнен в lifecycle_manager

            logger.info("Оптимизации производительности инициализированы")
        except Exception as e:
            logger.warning(f"Не удалось инициализировать оптимизации: {e}")

    @app.on_event("shutdown")
    async def shutdown_cleanup():
        """Очистка при завершении приложения"""
        try:
            # Очистка ресурсов
            performance_optimizer.optimize_memory_usage()
            logger.info("Ресурсы очищены")
        except Exception as e:
            logger.warning(f"Ошибка при очистке ресурсов: {e}")

    logger.info(
        "FastAPI application created and configured with performance optimizations"
    )
    return app


# Глобальный экземпляр приложения
_radius_app = create_app()
