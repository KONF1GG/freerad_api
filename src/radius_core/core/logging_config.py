"""Настройка логирования для Radius Core."""

import logging
import os
from typing import Optional

from ..config.settings import LOG_CONSOLE, LOG_FILE, LOG_LEVEL, EXTERNAL_LOG_LEVEL


def setup_logging(
    log_level: str = "INFO",
    external_log_level: str = "WARNING",
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """
    Настраивает логирование для Radius Core.

    Args:
        log_level: Уровень логирования для основного приложения
        external_log_level: Уровень логирования для внешних библиотек
        log_file: Путь к файлу логов (по умолчанию из переменной окружения)
        log_format: Кастомный формат логов

    Returns:
        Настроенный логгер
    """
    # Получаем уровни логирования
    app_level = getattr(logging, log_level.upper(), logging.INFO)
    ext_level = getattr(logging, external_log_level.upper(), logging.WARNING)

    # Путь к файлу логов
    if log_file is None:
        log_file = LOG_FILE

    # Создаем директорию для логов если её нет
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Формат логов совместимый с Promtail
    if log_format is None:
        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Создаем форматтер
    formatter = logging.Formatter(
        fmt=log_format,
        datefmt="%Y-%m-%d %H:%M:%S,%f"[:-3],  # YYYY-MM-DD HH:MM:SS,mmm
    )

    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(app_level)

    # Очищаем существующие хендлеры
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Файловый хендлер
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(app_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Консольный хендлер (только для отладки)
    if LOG_CONSOLE.lower() == "true":
        console_handler = logging.StreamHandler()
        console_handler.setLevel(app_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Настраиваем уровень для внешних библиотек
    external_libs = ["aio_pika", "redis", "fastapi", "uvicorn", "asyncio"]
    for lib in external_libs:
        logging.getLogger(lib).setLevel(ext_level)

    # Создаем и возвращаем логгер для текущего модуля
    logger = logging.getLogger(__name__)
    logger.info(
        "Логирование настроено: основной уровень %s, внешние библиотеки %s, файл: %s",
        log_level,
        external_log_level,
        log_file,
    )

    return logger


# Функция для быстрого получения логгера
def get_logger(name: str = None) -> logging.Logger:
    """
    Получает настроенный логгер.

    Args:
        name: Имя логгера (по умолчанию __name__)

    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name or __name__)


# Инициализация логирования при импорте модуля
if __name__ != "__main__":
    setup_logging(
        log_level=LOG_LEVEL,
        external_log_level=EXTERNAL_LOG_LEVEL,
        log_file=LOG_FILE,
    )
