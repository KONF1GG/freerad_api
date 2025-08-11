import functools
import time
import asyncio
import logging
from typing import Any, Callable, Optional, Dict

import metrics

logger = logging.getLogger(__name__)


def measure_time(
    operation: str, tags: Optional[Dict[str, str]] = None, record_as: str = "operation"
):
    """
    Декоратор для измерения времени выполнения функции

    Args:
        operation: Название операции для метрик
        tags: Дополнительные теги для метрик
        record_as: Тип записи метрики ('operation', 'external_call', 'database')
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            status = "success"
            error_type = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_type = type(e).__name__
                logger.error(f"Error in {operation}: {e}")
                raise
            finally:
                duration = time.time() - start_time

                # Подготавливаем теги
                final_tags = tags.copy() if tags else {}
                final_tags["status"] = status
                if error_type:
                    final_tags["error_type"] = error_type

                # Записываем метрики в зависимости от типа
                if record_as == "operation":
                    metrics.record_operation_duration(operation, duration, status)
                elif record_as == "external_call":
                    service = final_tags.get("service", "unknown")
                    metrics.record_external_call(service, operation, status, duration)
                elif record_as == "database":
                    table = final_tags.get("table", "unknown")
                    metrics.record_database_operation(
                        operation, table, duration, status
                    )
                else:
                    # Общая запись
                    metrics.metrics.record_timer(
                        f"{record_as}_{operation}_duration", duration, final_tags
                    )
                    metrics.metrics.increment_counter(
                        f"{record_as}_{operation}_total", final_tags
                    )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            status = "success"
            error_type = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                error_type = type(e).__name__
                logger.error(f"Error in {operation}: {e}")
                raise
            finally:
                duration = time.time() - start_time

                # Подготавливаем теги
                final_tags = tags.copy() if tags else {}
                final_tags["status"] = status
                if error_type:
                    final_tags["error_type"] = error_type

                # Записываем метрики
                metrics.metrics.record_timer(
                    f"{record_as}_{operation}_duration", duration, final_tags
                )
                metrics.metrics.increment_counter(
                    f"{record_as}_{operation}_total", final_tags
                )

        # Возвращаем подходящий wrapper в зависимости от типа функции
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def measure_redis_operation(operation: str):
    """Специализированный декоратор для Redis операций"""
    return measure_time(operation, tags={"service": "redis"}, record_as="external_call")


def measure_rabbitmq_operation(operation: str):
    """Специализированный декоратор для RabbitMQ операций"""
    return measure_time(
        operation, tags={"service": "rabbitmq"}, record_as="external_call"
    )


def measure_database_operation(operation: str, table: str):
    """Специализированный декоратор для операций с базой данных"""
    return measure_time(operation, tags={"table": table}, record_as="database")
