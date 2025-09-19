"""Клиенты для внешних сервисов."""

from .redis_client import (
    redis_health_check,
    execute_redis_command,
    execute_redis_pipeline,
    get_redis,
    redis_client,
)
from .rabbitmq_client import (
    rabbitmq_health_check,
    rmq_send_message,
)

__all__ = [
    # Redis
    "redis_health_check",
    "redis_client",
    "execute_redis_command",
    "execute_redis_pipeline",
    "get_redis",
    # RabbitMQ
    "rabbitmq_health_check",
    "rmq_send_message",
]
