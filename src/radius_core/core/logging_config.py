"""Конфигурация логирования для Radius Core."""

import logging

# Настройка базового логгера
logger = logging.getLogger(__name__)

# Настраиваем корневой логгер для всех модулей
root_logger = logging.getLogger()
if not root_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
