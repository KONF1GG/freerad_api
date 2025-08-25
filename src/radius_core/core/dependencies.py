"""
Зависимости для FastAPI приложения.
"""

from typing import AsyncGenerator, Annotated
from fastapi import Depends
from redis.asyncio import Redis
from aio_pika.abc import AbstractChannel

from ..clients.redis_client import redis_client
from ..clients.rabbitmq_client import get_rabbitmq_client


async def get_redis_connection() -> AsyncGenerator[Redis, None]:
    """Получение подключения к Redis."""

    try:
        yield redis_client
    finally:
        pass


async def get_rabbitmq_connection() -> AsyncGenerator[AbstractChannel, None]:
    """Получение подключения к RabbitMQ."""
    rabbitmq_client = await get_rabbitmq_client()
    channel = await rabbitmq_client.get_channel()
    try:
        yield channel
    finally:
        pass


RedisDependency = Annotated[Redis, Depends(get_redis_connection)]
RabbitMQDependency = Annotated[AbstractChannel, Depends(get_rabbitmq_connection)]
