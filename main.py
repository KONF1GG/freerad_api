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
# Создаем форматтер для логов
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Настройка корневого логгера для всего приложения
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Удаляем все существующие хендлеры
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Файловый хендлер для подробных логов приложения
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# Добавляем файловый хендлер к корневому логгеру
root_logger.addHandler(file_handler)

# Отключаем логи библиотек в файле, но разрешаем критические ошибки
logging.getLogger("aio_pika").setLevel(logging.ERROR)
logging.getLogger("aiormq").setLevel(logging.ERROR)
logging.getLogger("uvicorn").setLevel(logging.ERROR)  # Отключаем uvicorn логи в файле

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
async def do_acct(data: AccountingData):
    """Обработка RADIUS Accounting запросов"""
    logger.info(f"Processing accounting request for session: {data.Acct_Session_Id}")
    try:
        result = await process_accounting(data)
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
async def do_auth(data: AuthRequest):
    """Авторизация пользователя"""
    logger.info(f"Processing auth request for user: {data.User_Name}")
    try:
        result = await auth(data)
        logger.info(f"Auth request processed successfully for user: {data.User_Name}")
        return result
    except Exception as e:
        logger.error(
            f"Error processing authentication request for user {data.User_Name}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    # Настройка uvicorn для логирования только HTTP запросов в stdout
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        access_log=True,  # HTTP логи в stdout для docker logs
        log_level="info",
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                },
                "access": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.access": {
                    "handlers": ["access"],
                    "level": "INFO",
                    "propagate": False,
                },
            },
        },
    )
