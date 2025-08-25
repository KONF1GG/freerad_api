"""Конфигурация логирования для Radius Core."""

import logging
import os

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
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    # Консольный хендлер только для INFO и выше (HTTP запросы)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    root_logger.setLevel(logging.DEBUG)
