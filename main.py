import uvicorn
import uvloop
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from schemas import AccountingData, AccountingResponse, AuthRequest
from typing import Dict
from services import auth, process_accounting
from redis_client import close_redis, redis_health_check
from rabbitmq_client import close_rabbitmq, rabbitmq_health_check
import os

log_dir = "/var/log/radius_core"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "radius_log.log")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)

logging.getLogger("aio_pika").setLevel(logging.INFO)
logging.getLogger("aiormq").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Установка политики событийного цикла для ускорения
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


app = FastAPI(
    title="Async Radius Microservice",
    description="asynchronous RADIUS service",
    version="1.0.0",
    lifespan=lifespan,
)

# Настройка автоматических метрик Prometheus
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app)

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# Основной эндпоинт авторизации
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
    )
