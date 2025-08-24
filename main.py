import os
import logging
import asyncio
from typing import Dict, Any
import uvloop
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Info
from contextlib import asynccontextmanager

from config import PROMETHEUS_MULTIPROC_DIR
from schemas import AccountingData, AccountingResponse, AuthRequest, CorrectRequest
from services import auth, check_and_correct_services, process_accounting
from redis_client import (
    close_redis,
    redis_health_check,
    _redis_client,
)
from rabbitmq_client import close_rabbitmq, rabbitmq_health_check

from dependencies import RedisDependency, RabbitMQDependency

# Константы для удобства (логи, метрики)
LOG_DIR = "/var/log/radius_core/"
LOG_FILE = os.path.join(LOG_DIR, "radius_log.log")

# Создаём директории
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("radius_core")
logger.setLevel(logging.DEBUG)

# Форматтер
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Файловый хендлер
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

# Консольный хендлер для stdout
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)

logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)

# Установка политики событийного цикла для ускорения
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# Метрика для отслеживания воркеров
worker_info = Info("radius_worker", "Information about radius worker process")
worker_info.info({"pid": str(os.getpid())})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения.

    Включает health checks, очистку метрик для multiprocess и graceful shutdown.
    """
    logger.info("Starting Radius Core service...")

    try:
        logger.info(
            f"Attempting to configure Prometheus multiprocess dir: {PROMETHEUS_MULTIPROC_DIR}"
        )

        if PROMETHEUS_MULTIPROC_DIR:
            import shutil

            try:
                # Проверяем текущие права доступа
                parent_dir = os.path.dirname(PROMETHEUS_MULTIPROC_DIR)
                logger.info(
                    f"Parent directory: {parent_dir}, exists: {os.path.exists(parent_dir)}"
                )

                # Очищаем и создаем директорию для multiprocess метрик
                shutil.rmtree(PROMETHEUS_MULTIPROC_DIR, ignore_errors=True)
                os.makedirs(PROMETHEUS_MULTIPROC_DIR, exist_ok=True)

                # Проверяем, что директория действительно создана
                if os.path.exists(PROMETHEUS_MULTIPROC_DIR) and os.access(
                    PROMETHEUS_MULTIPROC_DIR, os.W_OK
                ):
                    # Устанавливаем переменную окружения для prometheus_client
                    os.environ["prometheus_multiproc_dir"] = PROMETHEUS_MULTIPROC_DIR

                    logger.info(
                        f"Prometheus multiprocess dir successfully configured: {PROMETHEUS_MULTIPROC_DIR}"
                    )
                else:
                    raise OSError(
                        f"Directory {PROMETHEUS_MULTIPROC_DIR} is not writable"
                    )

            except OSError as e:
                logger.warning(
                    f"Failed to create Prometheus multiprocess dir {PROMETHEUS_MULTIPROC_DIR}: {e}. "
                    "Metrics will work in single-process mode."
                )
                # Отключаем multiprocess режим, удаляем переменную окружения
                if "prometheus_multiproc_dir" in os.environ:
                    del os.environ["prometheus_multiproc_dir"]

        # Health checks
        if not await redis_health_check():
            logger.error("Redis health check failed")
            raise RuntimeError("Redis unavailable")
        else:
            logger.info("Redis connection established")

        if not await rabbitmq_health_check():
            logger.error("RabbitMQ health check failed")
            raise RuntimeError("RabbitMQ unavailable")
        else:
            logger.info("RabbitMQ connection established")

        yield

    finally:
        logger.info("Shutting down Radius Core service...")
        await close_redis()
        await close_rabbitmq()


app = FastAPI(
    title="Async Radius Microservice",
    description="Asynchronous RADIUS service",
    version="1.0.0",
    lifespan=lifespan,
)

# Настройка Prometheus для multiprocess режима
instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_group_untemplated=False,
    excluded_handlers=["/metrics"],
    should_instrument_requests_inprogress=True,
    should_respect_env_var=True,
    env_var_name="ENABLE_METRICS",
    inprogress_name="inprogress",
    inprogress_labels=True,
)

# Инструментируем приложение, но НЕ экспонируем стандартный эндпоинт
instrumentator.instrument(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/metrics")
async def get_metrics():
    """Кастомный эндпоинт для метрик с поддержкой multiprocess."""
    from prometheus_client import CollectorRegistry, multiprocess, generate_latest
    from fastapi import Response

    # Проверяем, доступен ли multiprocess режим
    multiprocess_available = (
        PROMETHEUS_MULTIPROC_DIR
        and "prometheus_multiproc_dir" in os.environ
        and os.path.exists(PROMETHEUS_MULTIPROC_DIR)
    )

    if multiprocess_available:
        try:
            # Создаем новый registry для multiprocess
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)

            # Генерируем метрики
            metrics_data = generate_latest(registry)
            return Response(content=metrics_data, media_type="text/plain")
        except Exception as e:
            logger.warning(
                f"Failed to collect multiprocess metrics: {e}. Falling back to single-process mode."
            )

    # Fallback на стандартные метрики если multiprocess не настроен или недоступен
    from prometheus_client import REGISTRY, generate_latest

    return Response(content=generate_latest(REGISTRY), media_type="text/plain")


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Проверка здоровья сервиса, включая статус подключений и Redis pool stats."""
    redis_ok = await redis_health_check()
    rabbitmq_ok = await rabbitmq_health_check()

    status = "healthy" if redis_ok and rabbitmq_ok else "unhealthy"

    if _redis_client._semaphore._waiters:
        waiting_tasks = (
            len(_redis_client._semaphore._waiters)
            if hasattr(_redis_client._semaphore, "_waiters")
            else 0
        )
    semaphore_value = (
        _redis_client._semaphore._value
        if hasattr(_redis_client._semaphore, "_value")
        else 0
    )
    if _redis_client._pool:
        max_connections = (
            _redis_client._pool.max_connections
            if hasattr(_redis_client, "_pool")
            else 0
        )

    return {
        "status": status,
        "worker_pid": os.getpid(),
        "services": {
            "redis": "ok" if redis_ok else "error",
            "rabbitmq": "ok" if rabbitmq_ok else "error",
        },
        "redis_pool": {
            "available_permits": semaphore_value,
            "waiting_tasks": waiting_tasks,
            "max_connections": max_connections,
        },
    }


@app.get("/")
async def root() -> Dict[str, str]:
    """Корневой эндпоинт для проверки статуса сервиса."""
    return {"service": "Radius Core", "version": "1.0.0", "status": "running"}


@app.post("/acct/", response_model=AccountingResponse)
async def do_acct(
    data: AccountingData, redis: RedisDependency, rabbitmq: RabbitMQDependency
) -> AccountingResponse:
    """Обработка RADIUS Accounting запросов.

    Пример: POST с AccountingData -> возвращает AccountingResponse.
    """
    logger.info(f"Processing accounting request for session: {data.Acct_Session_Id}")
    try:
        result = await process_accounting(data, redis, rabbitmq)
        logger.info(
            f"Accounting request processed successfully for session: {data.Acct_Session_Id}"
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error processing accounting request for session {data.Acct_Session_Id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/authorize/", response_model=Dict[str, Any])
async def do_auth(data: AuthRequest, redis: RedisDependency) -> Dict[str, Any]:
    """Авторизация пользователя.

    Пример: POST с AuthRequest -> возвращает dict с результатом.
    """
    logger.info(f"Processing auth request for user: {data.User_Name}")
    try:
        result = await auth(data, redis)
        logger.info(f"Auth request processed successfully for user: {data.User_Name}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error processing authentication request for user {data.User_Name}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/check_and_correct_services/", response_model=Dict[str, Any])
async def do_coa(
    data: CorrectRequest, redis: RedisDependency, rabbitmq: RabbitMQDependency
):
    """Обработка CoA (Change of Authorization) запроса.

    Пример: POST с CorrectRequest -> возвращает dict с результатом.
    """
    logger.info(f"Processing CoA request for user: {data.key}")
    try:
        result = await check_and_correct_services(data.key, redis, rabbitmq)
        logger.info(f"CoA request processed successfully for user: {data.key}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error processing CoA request for user {data.key}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


# if __name__ == "__main__":
#     uvicorn.run(
#         "main:app",
#         host="0.0.0.0",
#         port=8000,
#         reload=False,
#         access_log=True,
#         log_level="debug",
#         workers=3,
#     )
