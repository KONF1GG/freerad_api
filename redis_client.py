import redis.asyncio as redis
import asyncio
import logging
from typing import Optional
from contextlib import asynccontextmanager
from config import (
    REDIS_URL,
    REDIS_POOL_SIZE,
    REDIS_CONCURRENCY,
    REDIS_COMMAND_TIMEOUT,
    REDIS_WARNING_THRESHOLD,
)

from redis.retry import Retry
from redis.backoff import ExponentialBackoff

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self):
        self._pool: Optional[redis.ConnectionPool] = None
        self._redis: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()
        # Семафор для ограничения одновременных операций
        self._semaphore = asyncio.Semaphore(REDIS_CONCURRENCY)

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
                            health_check_interval=15,
                            socket_connect_timeout=5,
                            socket_timeout=5,
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


@asynccontextmanager
async def get_redis_connection_optimized():
    """Оптимизированный контекстный менеджер для пакетных операций"""
    redis_client = await _redis_client.get_client()
    try:
        yield redis_client
    finally:
        # Соединение возвращается в пул автоматически
        pass


async def get_redis() -> redis.Redis:
    """Получить Redis клиент"""
    waiting_tasks = (
        len(_redis_client._semaphore._waiters)
        if _redis_client._semaphore._waiters
        else 0
    )
    if waiting_tasks > REDIS_WARNING_THRESHOLD:
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


async def execute_redis_command(redis_client, *args, timeout: float | None = None):
    """Выполнить команду Redis с тайм-аутом"""
    eff_timeout = timeout if timeout is not None else REDIS_COMMAND_TIMEOUT
    try:
        async with _redis_client._semaphore:
            result = await asyncio.wait_for(
                redis_client.execute_command(*args), timeout=eff_timeout
            )
        return result
    except asyncio.TimeoutError:
        logger.error(f"Redis command timeout after {eff_timeout}s: {args[0]}")
        raise
    except Exception as e:
        logger.error(f"Redis command error: {e}")
        raise


async def execute_redis_pipeline(
    commands: list, timeout: float | None = None, redis_client=None
):
    """Выполнить пакет команд Redis через pipeline для повышения производительности"""
    eff_timeout = timeout if timeout is not None else REDIS_COMMAND_TIMEOUT

    try:
        if redis_client is None:
            async with _redis_client._semaphore:
                redis_client = await _redis_client.get_client()
                pipe = redis_client.pipeline()

                # Добавляем команды в pipeline
                for cmd in commands:
                    pipe.execute_command(*cmd)

                # Выполняем весь пакет
                result = await asyncio.wait_for(pipe.execute(), timeout=eff_timeout)
        else:
            # Используем переданный клиент
            pipe = redis_client.pipeline()

            # Добавляем команды в pipeline
            for cmd in commands:
                pipe.execute_command(*cmd)

            # Выполняем весь пакет
            result = await asyncio.wait_for(pipe.execute(), timeout=eff_timeout)

        return result
    except asyncio.TimeoutError:
        logger.error(f"Redis pipeline timeout after {eff_timeout}s")
        raise
    except Exception as e:
        logger.error(f"Redis pipeline error: {e}")
        raise
