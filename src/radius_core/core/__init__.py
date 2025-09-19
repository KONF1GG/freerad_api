"""
Основные модули Radius Core.

Логирование настраивается автоматически при импорте logging_config.
"""

# Логирование настраивается автоматически при импорте
from . import logging_config

# Импортируем основные модули
from .app_factory import create_app
from .app_lifecycle import lifecycle_manager
from .dependencies import get_redis_connection, get_rabbitmq_connection
from .metrics import metrics_manager

__all__ = [
    "create_app",
    "lifecycle_manager",
    "get_redis_connection",
    "get_rabbitmq_connection",
    "metrics_manager",
]
