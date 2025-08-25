"""Конфигурация логирования для Radius Core."""

import logging

# Настройка базового логгера
logger = logging.getLogger("radius_core")

# Если логгер еще не настроен, настраиваем его
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
