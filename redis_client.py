import redis.asyncio as redis
import asyncio
import logging
from typing import Optional
from contextlib import asynccontextmanager
from config import REDIS_URL, REDIS_POOL_SIZE

from redis.retry import Retry
from redis.backoff import ExponentialBackoff

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self._pool: Optional[redis.ConnectionPool] = None
        self._redis: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()
        # Семафор для ограничения одновременных операций
        self._semaphore = asyncio.Semaphore(REDIS_POOL_SIZE)

    async def get_client(self) -> redis.Redis:
        """Получить Redis клиент с пулом соединений"""
        if self._redis is None:
            async with self._lock:
                if self._redis is None:
                    try:
                        self._pool = redis.ConnectionPool.from_url(
                            REDIS_URL,
                            max_connections=REDIS_POOL_SIZE,
                            decode_responses=True,
                            retry_on_timeout=True,
                            retry=Retry(
                                retries=3,
                                backoff=ExponentialBackoff(base=0.1, cap=1.0),
                                supported_errors=(ConnectionError, TimeoutError),
                            ),
                            socket_keepalive=True,
                            socket_keepalive_options={},
                            health_check_interval=30,
                            socket_connect_timeout=10,
                            socket_timeout=10,
                        )
                        self._redis = redis.Redis(connection_pool=self._pool)
                        # Проверяем соединение
                        await self._redis.ping()
                        logger.info("Redis connection established successfully")
                    except Exception as e:
                        logger.error(f"Failed to connect to Redis: {e}")
                        raise
        return self._redis

    async def close(self):
        """Закрыть соединение с Redis"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        if self._pool:
            await self._pool.disconnect()
            self._pool = None

    async def health_check(self) -> bool:
        """Проверка здоровья соединения"""
        try:
            redis_client = await self.get_client()
            await redis_client.ping()
            # Логируем информацию о пуле соединений
            if self._pool:
                logger.debug(
                    f"Redis pool max_connections: {self._pool.max_connections}"
                )
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


# Глобальный экземпляр клиента
_redis_client = RedisClient()


@asynccontextmanager
async def get_redis_connection():
    """Контекстный менеджер для получения Redis соединения с ограничением одновременных операций"""
    async with _redis_client._semaphore:
        redis_client = await _redis_client.get_client()
        try:
            yield redis_client
        finally:
            # Соединение возвращается в пул автоматически
            pass


async def get_redis() -> redis.Redis:
    """Получить Redis клиент"""
    # Логируем количество задач, ожидающих семафор
    waiting_tasks = (
        len(_redis_client._semaphore._waiters)
        if _redis_client._semaphore._waiters
        else 0
    )
    if waiting_tasks > 5:
        logger.warning(
            f"High Redis connection contention: {waiting_tasks} tasks waiting"
        )

    return await _redis_client.get_client()


async def close_redis():
    """Закрыть Redis соединение"""
    await _redis_client.close()


async def redis_health_check() -> bool:
    """Проверка здоровья Redis"""
    return await _redis_client.health_check()


async def execute_redis_command(redis_client, *args, timeout: float = 5.0):
    """Выполнить команду Redis с тайм-аутом"""
    try:
        return await asyncio.wait_for(
            redis_client.execute_command(*args), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error(f"Redis command timeout after {timeout}s: {args[0]}")
        raise
    except Exception as e:
        logger.error(f"Redis command error: {e}")
        raise
