import os
import uvicorn
import uvloop
import asyncio
import logging
from logging.handlers import QueueHandler, QueueListener
from queue import SimpleQueue
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from schemas import AccountingData, AccountingResponse, AuthRequest
from typing import Dict
from services import auth, process_accounting
from redis_client import close_redis, redis_health_check
from rabbitmq_client import close_rabbitmq, rabbitmq_health_check
from metrics import get_metrics_summary, get_prometheus_metrics

from middleware.base import EnhancedMetricsMiddleware

# Неблокирующее логирование через очередь (минимизирует блокировки event loop)
_log_queue = SimpleQueue()

# File handler for logging

os.makedirs("/var/log/radius_core", exist_ok=True)
_file_handler = logging.FileHandler("/var/log/radius_core/radius_core.log")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.handlers = [QueueHandler(_log_queue)]
_log_listener = QueueListener(_log_queue, _file_handler)
_log_listener.start()

logging.getLogger("aio_pika").setLevel(logging.INFO)
logging.getLogger("aiormq").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Установка политики событийного цикла
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    logger.info("Starting Radius Core service...")

    # Проверяем подключения при старте
    if not await redis_health_check():
        logger.error("Redis health check failed")
    else:
        logger.info("Redis connection established")

    if not await rabbitmq_health_check():
        logger.error("RabbitMQ health check failed")
    else:
        logger.info("RabbitMQ connection established")

    yield

    logger.info("Shutting down Radius Core service...")
    await close_redis()
    await close_rabbitmq()
    try:
        _log_listener.stop()
    except Exception:
        pass


app = FastAPI(
    title="Async Radius Microservice",
    description="asynchronous RADIUS service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(EnhancedMetricsMiddleware)

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/metrics")
async def metrics():
    """Метрики сервиса"""
    return get_metrics_summary()


@app.get("/metrics_prom")
async def metrics_prometheus():
    """Prometheus-совместимые метрики (для скрейпа Prometheus/Grafana)."""
    payload, content_type = get_prometheus_metrics()
    return Response(content=payload, media_type=content_type)


@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    redis_ok = await redis_health_check()
    rabbitmq_ok = await rabbitmq_health_check()

    status = "healthy" if redis_ok and rabbitmq_ok else "unhealthy"

    # Добавляем информацию о Redis семафоре
    from redis_client import _redis_client

    waiting_tasks = (
        len(_redis_client._semaphore._waiters)
        if _redis_client._semaphore._waiters
        else 0
    )
    semaphore_value = _redis_client._semaphore._value

    return {
        "status": status,
        "services": {
            "redis": "ok" if redis_ok else "error",
            "rabbitmq": "ok" if rabbitmq_ok else "error",
        },
        "redis_pool": {
            "available_permits": semaphore_value,
            "waiting_tasks": waiting_tasks,
            "max_connections": _redis_client._pool.max_connections
            if _redis_client._pool
            else 0,
        },
    }


@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {"service": "Radius Core", "version": "1.0.0", "status": "running"}


# Основной эндпоинт аккаунтинга
@app.post("/acct/", response_model=AccountingResponse)
async def do_acct(data: AccountingData):
    """Обработка RADIUS Accounting запросов"""
    try:
        return await process_accounting(data)
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing accounting request: {http_exc}", exc_info=True
        )
        raise
    except Exception as e:
        logger.error(f"Error processing accounting request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/authorize/", response_model=Dict)
async def do_auth(data: AuthRequest):
    """Авторизация пользователя"""
    try:
        return await auth(data)
    except Exception as e:
        logger.error(f"Error processing authentication request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        access_log=True,
        log_level="debug",
        workers=2,
    )
