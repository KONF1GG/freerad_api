"""
Модуль с API эндпоинтами Radius Core.

Включает:
- Эндпоинт для метрик
- Health check эндпоинт
- Основные RADIUS эндпоинты
"""

import os
import logging
from typing import Dict, Any
from fastapi import APIRouter, HTTPException

from radius_core.services.storage.search_operations import find_login_by_session
from ..models.schemas import (
    AccountingData,
    AccountingResponse,
    AuthRequest,
    CorrectRequest,
)
from ..services import auth, check_and_correct_services, process_accounting

from ..clients import redis_health_check, redis_client
from ..clients import rabbitmq_health_check

from ..core.dependencies import RedisDependency, RabbitMQDependency
from ..core.metrics import metrics_manager, track_function, track_http_request
from ..config import PROMETHEUS_MULTIPROC_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics")
@track_http_request(method="GET", endpoint="/metrics")
async def get_metrics():
    """Эндпоинт для метрик с поддержкой multiprocess aggregation."""
    return await metrics_manager.get_metrics(PROMETHEUS_MULTIPROC_DIR)


@router.get("/health")
@track_http_request(method="GET", endpoint="/health")
async def health_check() -> Dict[str, Any]:
    """Проверка здоровья сервиса, включая статус подключений и Redis pool stats."""

    redis_ok = await redis_health_check()
    rabbitmq_ok = await rabbitmq_health_check()

    status = "healthy" if redis_ok and rabbitmq_ok else "unhealthy"

    # Получение статистики Redis pool
    waiting_tasks = 0
    semaphore_value = 0
    max_connections = 0

    # Детальная информация о Redis пуле
    redis_pool_detailed = {}

    try:
        semaphore = getattr(redis_client, "_semaphore", None)
        if semaphore:
            waiters = getattr(semaphore, "_waiters", None)
            if waiters:
                waiting_tasks = len(waiters)

            semaphore_value = getattr(semaphore, "_value", 0)

            redis_pool_detailed["available_permits"] = semaphore_value
            redis_pool_detailed["waiting_tasks"] = waiting_tasks

        pool = getattr(redis_client, "_pool", None)
        if pool:
            max_connections = getattr(pool, "max_connections", 0)

            # Попытка получить детальную информацию о пуле
            try:
                # Активные соединения (в использовании)
                active_connections = len(getattr(pool, "_in_use_connections", []))
                # Свободные соединения
                idle_connections = len(getattr(pool, "_available_connections", []))
                # Процент использования
                usage_percent = (
                    (active_connections / max_connections) * 100
                    if max_connections > 0
                    else 0
                )

                redis_pool_detailed.update(
                    {
                        "max_connections": max_connections,
                        "active_connections": active_connections,
                        "idle_connections": idle_connections,
                        "connection_usage_percent": round(usage_percent, 2),
                        "total_connections": active_connections + idle_connections,
                    }
                )
            except (AttributeError, TypeError):
                # Если не удалось получить детальную информацию
                redis_pool_detailed["max_connections"] = max_connections

    except (AttributeError, TypeError):
        pass

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
        "redis_pool_detailed": redis_pool_detailed,
    }


@router.get("/")
@track_http_request(method="GET", endpoint="/")
async def root() -> Dict[str, str]:
    """Корневой эндпоинт для проверки статуса сервиса."""
    return {"service": "Radius Core", "version": "1.0.0", "status": "running"}


@router.post("/acct/", response_model=AccountingResponse)
@track_function("radius", "acct")
@track_http_request(method="POST", endpoint="/acct/")
async def do_acct(
    data: AccountingData, redis: RedisDependency, rabbitmq: RabbitMQDependency
) -> AccountingResponse:
    """Обработка RADIUS Accounting запросов."""
    logger.info("Обработка запроса учета для сессии: %s", data.Acct_Unique_Session_Id)
    try:
        result = await process_accounting(data, redis, rabbitmq)
        logger.info(
            "Запрос учета успешно обработан для сессии: %s",
            data.Acct_Unique_Session_Id,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Ошибка при обработке запроса учета для сессии %s: %s",
            data.Acct_Unique_Session_Id,
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/authorize/", response_model=Dict[str, Any])
@track_function("radius", "auth")
@track_http_request(method="POST", endpoint="/authorize/")
async def do_auth(data: AuthRequest, redis: RedisDependency) -> Dict[str, Any]:
    """Авторизация пользователя."""
    logger.info("Обработка запроса авторизации для пользователя: %s", data.User_Name)
    try:
        result = await auth(data, redis)
        logger.info(
            "Запрос авторизации успешно обработан для пользователя: %s", data.User_Name
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Ошибка при обработке запроса авторизации для пользователя %s: %s",
            data.User_Name,
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/check_and_correct_services/", response_model=Dict[str, Any])
@track_function("radius", "check_services")
@track_http_request(method="POST", endpoint="/check_and_correct_services/")
async def do_check_and_correct_services(
    data: CorrectRequest, redis: RedisDependency, rabbitmq: RabbitMQDependency
):
    """Корректирует услуги при расхождениях"""
    logger.info("Обработка запроса CoA для пользователя: %s", data.key)
    try:
        result = await check_and_correct_services(data.key, redis, rabbitmq)
        logger.info("Запрос CoA успешно обработан для пользователя: %s", data.key)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Ошибка при обработке запроса CoA для пользователя %s: %s",
            data.key,
            e,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


# @router.post("/find_login_by_session/", response_model=Dict[str, Any])
# @track_function("radius", "find_login_by_session")
# @track_http_request(method="POST", endpoint="/find_login_by_session/")
# async def find_login_by_session_endpoint(
#     data: AuthRequest, redis: RedisDependency
# ) -> Dict[str, Any]:
#     """
#     Выполняет поиск логина по данным сессии.
#     """
#     logger.info(
#         "Обработка запроса поиска логина по сессии для пользователя: %s", data.User_Name
#     )
#     try:
#         result = await find_login_by_session(data, redis)
#         if result is None:
#             logger.info("Логин не найден для пользователя: %s", data.User_Name)
#             return {"result": None}
#         logger.info("Логин найден для пользователя: %s", data.User_Name)
#         return {"result": result.model_dump()}
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(
#             "Ошибка при поиске логина по сессии для пользователя %s: %s",
#             data.User_Name,
#             e,
#             exc_info=True,
#         )
#         raise HTTPException(status_code=500, detail=str(e)) from e
