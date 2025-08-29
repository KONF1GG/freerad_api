"""Простая настройка логирования для Radius Core."""

import logging
import os
from ..config.settings import MAIN_LOG_LEVEL, EXTERNAL_LOG_LEVEL

# Получаем уровень логирования
log_level = getattr(logging, MAIN_LOG_LEVEL.upper(), logging.INFO)
external_level = getattr(logging, EXTERNAL_LOG_LEVEL.upper(), logging.WARNING)

# Настраиваем корневой логгер
root_logger = logging.getLogger()
root_logger.setLevel(log_level)

# Очищаем существующие хендлеры
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Файловый хендлер
log_file = os.getenv("LOG_FILE", "/var/log/radius_core/radius_log.log")
log_dir = os.path.dirname(log_file)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(log_level)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
root_logger.addHandler(file_handler)

# Консольный хендлер
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
root_logger.addHandler(console_handler)

# Устанавливаем уровень для внешних библиотек
external_libs = ["aio_pika", "redis", "fastapi", "uvicorn"]
for lib in external_libs:
    logging.getLogger(lib).setLevel(external_level)

# Логгер для текущего модуля
logger = logging.getLogger(__name__)
logger.info(
    "Логирование настроено: основной уровень %s, внешние библиотеки %s",
    MAIN_LOG_LEVEL,
    EXTERNAL_LOG_LEVEL,
)
