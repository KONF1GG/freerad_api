"""
Модуль для работы с Redis.

Содержит класс RedisClient для работы с Redis,
а также функции для получения соединения, закрытия соединения и проверки здоровья соединения.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as redis
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from ..config import (
    REDIS_URL,
    REDIS_POOL_SIZE,
    REDIS_CONCURRENCY,
    REDIS_COMMAND_TIMEOUT,
)

logger = logging.getLogger(__name__)


class RedisClient:
    """Класс для работы с Redis"""

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
                        logger.info("Соединение с Redis успешно установлено")
                    except Exception:
                        logger.error("Не удалось установить соединение с Redis")
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
            redis_conn = await self.get_client()
            await redis_conn.ping()
            # Логируем информацию о пуле соединений
            if self._pool:
                # logger.debug(
                #     "Redis pool max_connections: %s", self._pool.max_connections
                # )
                pass
            return True
        except (ConnectionError, TimeoutError) as e:
            logger.error("Проверка здоровья соединения с Redis не прошла: %s", e)
            return False

    @property
    def semaphore(self):
        """Получить семафор для ограничения одновременных операций"""
        return self._semaphore

    def __getattr__(self, name):
        """Делегируем все неизвестные методы к обычному Redis клиенту"""
        if self._redis is None:
            raise AttributeError(f"Redis client not initialized, cannot access {name}")
        return getattr(self._redis, name)


# Глобальный экземпляр клиента
redis_client = RedisClient()


@asynccontextmanager
async def get_redis_connection():
    """Контекстный менеджер для получения Redis соединения"""
    redis_client_conn = await redis_client.get_client()
    try:
        yield redis_client_conn
    finally:
        # Соединение возвращается в пул автоматически
        pass


async def get_redis() -> redis.Redis:
    """Получить Redis клиент"""

    return await redis_client.get_client()


async def close_redis():
    """Закрыть Redis соединение"""
    await redis_client.close()


async def redis_health_check() -> bool:
    """Проверка здоровья Redis"""
    return await redis_client.health_check()


async def execute_redis_command(redis_conn, *args, timeout: float | None = None):
    """Выполнить команду Redis с тайм-аутом"""
    eff_timeout = timeout if timeout is not None else REDIS_COMMAND_TIMEOUT
    command_name = args[0] if args else "unknown"
    command_args = args[1:] if len(args) > 1 else []

    try:
        # logger.debug(
        #     "Executing Redis command: %s with args: %s",
        #     command_name,
        #     command_args,
        # )
        result = await asyncio.wait_for(
            redis_conn.execute_command(*args), timeout=eff_timeout
        )
        # logger.debug("Redis command result: %s", result)
        return result
    except asyncio.TimeoutError:
        logger.error(
            "Redis команда завершилась с тайм-аутом: %s: %s (аргументы: %s)",
            eff_timeout,
            command_name,
            command_args,
        )
        raise
    except Exception as e:
        logger.error(
            "Redis команда завершилась с ошибкой: %s\n"
            "Команда: %s\n"
            "Аргументы: %s\n"
            "Тип ошибки: %s",
            e,
            command_name,
            command_args,
            type(e).__name__,
        )
        raise


async def execute_redis_pipeline(
    commands: list, timeout: float | None = None, redis_conn=None
):
    """Выполнить пакет команд Redis через pipeline для повышения производительности"""
    eff_timeout = timeout if timeout is not None else REDIS_COMMAND_TIMEOUT

    try:
        if redis_conn is None:
            redis_client_conn = await redis_client.get_client()
            pipe = redis_client_conn.pipeline()

            # Добавляем команды в pipeline
            for cmd in commands:
                pipe.execute_command(*cmd)

            # Выполняем весь пакет
            result = await asyncio.wait_for(pipe.execute(), timeout=eff_timeout)
        else:
            # Используем переданный клиент
            pipe = redis_conn.pipeline()

            # Добавляем команды в pipeline
            for cmd in commands:
                pipe.execute_command(*cmd)

            # Выполняем весь пакет
            result = await asyncio.wait_for(pipe.execute(), timeout=eff_timeout)

        return result
    except asyncio.TimeoutError:
        logger.error(
            "Redis pipeline завершился с тайм-аутом: %s\n"
            "Количество команд: %s\n"
            "Первая команда: %s",
            eff_timeout,
            len(commands),
            commands[0] if commands else "нет команд",
        )
        raise
    except Exception as e:
        logger.error(
            "Redis pipeline завершился с ошибкой: %s\n"
            "Количество команд: %s\n"
            "Команды: %s\n"
            "Тип ошибки: %s",
            e,
            len(commands),
            commands,
            type(e).__name__,
        )
        raise
