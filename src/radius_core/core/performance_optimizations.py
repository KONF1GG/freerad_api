"""
Дополнительные оптимизации производительности для Radius Core.

Включает:
- Connection pooling оптимизации
- Batch операции
- Кэширование
- Асинхронные оптимизации
"""

import asyncio
import logging
from typing import List, Any, Dict, Optional
from functools import lru_cache
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class PerformanceOptimizer:
    """Класс для оптимизации производительности"""

    def __init__(self):
        self._connection_pools = {}
        self._cache = {}
        self._batch_operations = {}

    @staticmethod
    async def batch_redis_operations(
        operations: List[tuple], redis_client, batch_size: int = 100
    ):
        """Выполнение пакетных Redis операций для повышения производительности"""
        if not operations:
            return []

        results = []
        for i in range(0, len(operations), batch_size):
            batch = operations[i : i + batch_size]
            try:
                # Используем pipeline для пакетных операций
                pipe = redis_client.pipeline()
                for op, *args in batch:
                    getattr(pipe, op)(*args)

                batch_results = await pipe.execute()
                results.extend(batch_results)

            except Exception as e:
                logger.error(f"Ошибка в пакетной Redis операции: {e}")
                # Fallback к индивидуальным операциям
                for op, *args in batch:
                    try:
                        result = await getattr(redis_client, op)(*args)
                        results.append(result)
                    except Exception as e2:
                        logger.error(
                            f"Ошибка в индивидуальной Redis операции {op}: {e2}"
                        )
                        results.append(None)

        return results

    @staticmethod
    async def batch_rabbitmq_operations(
        messages: List[tuple], rabbitmq_client, batch_size: int = 50
    ):
        """Отправка пакетных сообщений в RabbitMQ"""
        if not messages:
            return []

        results = []
        for i in range(0, len(messages), batch_size):
            batch = messages[i : i + batch_size]
            try:
                # Отправляем сообщения параллельно в рамках батча
                batch_tasks = [
                    rabbitmq_client.send_message(routing_key, message)
                    for routing_key, message in batch
                ]
                batch_results = await asyncio.gather(
                    *batch_tasks, return_exceptions=True
                )
                results.extend(batch_results)

            except Exception as e:
                logger.error(f"Ошибка в пакетной RabbitMQ операции: {e}")
                # Fallback к индивидуальным операциям
                for routing_key, message in batch:
                    try:
                        result = await rabbitmq_client.send_message(
                            routing_key, message
                        )
                        results.append(result)
                    except Exception as e2:
                        logger.error(f"Ошибка в индивидуальной RabbitMQ операции: {e2}")
                        results.append(False)

        return results

    @staticmethod
    def create_connection_pool(max_connections: int = 100, max_keepalive: int = 30):
        """Создание пула соединений с оптимизированными настройками"""
        return {
            "max_connections": max_connections,
            "max_keepalive": max_keepalive,
            "connection_timeout": 2.0,
            "read_timeout": 1.0,
            "write_timeout": 1.0,
        }

    @staticmethod
    async def async_retry_with_backoff(
        func, max_retries: int = 3, base_delay: float = 0.1, max_delay: float = 1.0
    ):
        """Асинхронный retry с экспоненциальным backoff"""
        for attempt in range(max_retries):
            try:
                return await func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e

                delay = min(base_delay * (2**attempt), max_delay)
                await asyncio.sleep(delay)

    @staticmethod
    def optimize_memory_usage():
        """Оптимизация использования памяти"""
        import gc

        # Принудительная сборка мусора
        gc.collect()

        # Настройка порогов сборки мусора
        gc.set_threshold(700, 10, 10)

    @staticmethod
    async def warm_up_connections(redis_client, rabbitmq_client):
        """Прогрев соединений для уменьшения latency"""
        try:
            # Прогрев Redis
            await redis_client.ping()
            logger.info("Redis соединение прогрето")

            # Прогрев RabbitMQ
            await rabbitmq_client.health_check()
            logger.info("RabbitMQ соединение прогрето")

        except Exception as e:
            logger.warning(f"Ошибка при прогреве соединений: {e}")


# Глобальный экземпляр оптимизатора
performance_optimizer = PerformanceOptimizer()


@asynccontextmanager
async def optimized_operation_context():
    """Контекст для оптимизированных операций"""
    try:
        # Оптимизация памяти перед операцией
        performance_optimizer.optimize_memory_usage()
        yield
    finally:
        # Очистка после операции
        pass


async def execute_with_optimization(func, *args, **kwargs):
    """Выполнение функции с оптимизациями"""
    async with optimized_operation_context():
        return await func(*args, **kwargs)


# Декоратор для оптимизации производительности
def optimize_performance(max_retries: int = 2, batch_size: int = 100):
    """Декоратор для оптимизации производительности функций"""

    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await performance_optimizer.async_retry_with_backoff(
                    lambda: func(*args, **kwargs), max_retries=max_retries
                )
            except Exception as e:
                logger.error(f"Ошибка в оптимизированной функции {func.__name__}: {e}")
                raise

        return wrapper

    return decorator
