from typing import Dict
import uvicorn
import uvloop
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from schemas import AccountingData, AccountingResponse, AuthRequest, AuthResponse
from services import auth, process_accounting
from redis_client import close_redis, redis_health_check
from rabbitmq_client import close_rabbitmq, rabbitmq_health_check
from metrics import get_metrics_summary

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("radius_core.log")],
)

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


app = FastAPI(
    title="Async Radius Accounting Microservice",
    description="High-performance asynchronous RADIUS accounting service",
    version="1.0.0",
    lifespan=lifespan,
)

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


@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    redis_ok = await redis_health_check()
    rabbitmq_ok = await rabbitmq_health_check()

    status = "healthy" if redis_ok and rabbitmq_ok else "unhealthy"

    return {
        "status": status,
        "services": {
            "redis": "ok" if redis_ok else "error",
            "rabbitmq": "ok" if rabbitmq_ok else "error",
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
    except Exception as e:
        logger.error(f"Error processing accounting request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/authorize/", response_model=AuthResponse)
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
        reload=True,
        access_log=True,
        log_level="debug",
    )
