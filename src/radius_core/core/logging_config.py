"""Конфигурация логирования для Radius Core."""

import logging
import os
from ..config.settings import EXTERNAL_LOG_LEVEL, ENABLE_EXTERNAL_LOGGER_CONFIG

# Настройка базового логгера
logger = logging.getLogger(__name__)

# Получаем путь к файлу логов из переменной окружения
log_file = os.getenv("LOG_FILE", "/var/log/radius_core/radius_log.log")

# Создаем директорию для логов если не существует
log_dir = os.path.dirname(log_file)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# Настраиваем корневой логгер для всех модулей
root_logger = logging.getLogger()
if not root_logger.handlers:
    # Файловый хендлер для всех логов
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    # Консольный хендлер только для INFO и выше (HTTP запросы)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    root_logger.setLevel(logging.INFO)


# Настройка уровня логирования для внешних библиотек
def configure_external_loggers():
    """Настройка уровня логирования для внешних библиотек."""
    if not ENABLE_EXTERNAL_LOGGER_CONFIG:
        return

    external_loggers = [
        "aio_pika",
        "pika",
        "redis",
        "aioredis",
        "elasticsearch",
        "elasticsearch_dsl",
        "urllib3",
        "httpx",
        "asyncio",
        "uvloop",
        "fastapi",
        "uvicorn",
        "prometheus_client",
        "prometheus_fastapi_instrumentator",
    ]

    # Преобразуем строку уровня логирования в константу
    log_level = getattr(logging, EXTERNAL_LOG_LEVEL.upper(), logging.WARNING)

    for logger_name in external_loggers:
        logging.getLogger(logger_name).setLevel(log_level)
        logger.info(
            "Установлен уровень логирования %s для %s",
            EXTERNAL_LOG_LEVEL,
            logger_name,
        )


# Вызываем настройку внешних логгеров
configure_external_loggers()
