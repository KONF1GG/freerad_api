from typing import AsyncGenerator, Annotated
from fastapi import Depends
from redis.asyncio import Redis
from aio_pika.abc import AbstractChannel

from redis_client import get_redis
from rabbitmq_client import get_rabbitmq_client



async def get_redis_connection() -> AsyncGenerator[Redis, None]:
    """Получение подключения к Redis."""
    redis_client = await get_redis()
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
