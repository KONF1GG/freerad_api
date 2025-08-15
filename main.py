import os
import logging
import asyncio
from typing import Dict
import uvicorn
import uvloop
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from contextlib import asynccontextmanager

from schemas import AccountingData, AccountingResponse, AuthRequest, CorrectRequest
from services import auth, check_and_correct_services, process_accounting
from redis_client import close_redis, redis_health_check
from rabbitmq_client import close_rabbitmq, rabbitmq_health_check
from dependencies import RedisDependency, RabbitMQDependency

log_dir = "~/radius_core/"

os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "radius_log.log")

# Настройка логирования
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Настройка корневого логгера для всего приложения
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Удаляем все существующие хендлеры
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Файловый хендлер для подробных логов приложения
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)

# Добавляем файловый хендлер к корневому логгеру
root_logger.addHandler(file_handler)

# Отключаем логи библиотек в файле, но разрешаем критические ошибки
logging.getLogger("aio_pika").setLevel(logging.ERROR)
logging.getLogger("aiormq").setLevel(logging.ERROR)
logging.getLogger("uvicorn").setLevel(logging.ERROR)

# Получаем логгер для этого модуля
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
async def do_acct(
    data: AccountingData, redis: RedisDependency, rabbitmq: RabbitMQDependency
):
    """Обработка RADIUS Accounting запросов"""
    logger.info(f"Processing accounting request for session: {data.Acct_Session_Id}")
    try:
        result = await process_accounting(data, redis, rabbitmq)
        logger.info(
            f"Accounting request processed successfully for session: {data.Acct_Session_Id}"
        )
        return result
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing accounting request for session {data.Acct_Session_Id}: {http_exc}",
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            f"Error processing accounting request for session {data.Acct_Session_Id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


# Основной эндпоинт авторизации
@app.post("/authorize/", response_model=Dict)
async def do_auth(data: AuthRequest, redis: RedisDependency):
    """Авторизация пользователя"""
    logger.info(f"Processing auth request for user: {data.User_Name}")
    try:
        result = await auth(data, redis)
        logger.info(f"Auth request processed successfully for user: {data.User_Name}")
        return result
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing authentication request for user {data.User_Name}: {http_exc}",
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            f"Error processing authentication request for user {data.User_Name}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e))


# Эндпоинт для проверки включенных сервисов
@app.post("/check_and_correct_services/", response_model=Dict)
async def do_coa(data: CorrectRequest, redis: RedisDependency):
    """Обработка CoA (Change of Authorization) запроса"""
    logger.info(f"Processing CoA request for user: {data.key}")
    try:
        result = await check_and_correct_services(data.key, redis)
        logger.info(f"CoA request processed successfully for user: {data.key}")
        return result
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing CoA request for user {data.key}: {http_exc}",
            exc_info=True,
        )
        raise
    except Exception as e:
        logger.error(
            f"Error processing CoA request for user {data.key}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        access_log=True,  # HTTP логи в stdout для docker logs
        log_level="debug",
    )
