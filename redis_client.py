import redis.asyncio as redis
import asyncio
import logging
from typing import Optional
from config import REDIS_URL, REDIS_POOL_SIZE

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self._pool: Optional[redis.ConnectionPool] = None
        self._redis: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()

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
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


# Глобальный экземпляр клиента
_redis_client = RedisClient()


async def get_redis() -> redis.Redis:
    """Получить Redis клиент"""
    return await _redis_client.get_client()


async def close_redis():
    """Закрыть Redis соединение"""
    await _redis_client.close()


async def redis_health_check() -> bool:
    """Проверка здоровья Redis"""
    return await _redis_client.health_check()
