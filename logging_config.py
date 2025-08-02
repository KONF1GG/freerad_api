import logging
import sys
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """JSON форматтер для логов"""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Добавляем исключения, если есть
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Добавляем дополнительные поля
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "getMessage",
                "exc_info",
                "exc_text",
                "stack_info",
            ):
                log_entry[key] = value

        return str(log_entry)


def setup_logging(level: str = "INFO", json_format: bool = False):
    """Настройка логирования"""

    # Уровни логирования
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")

    # Создаем форматтер
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Удаляем существующие обработчики
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Добавляем консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Добавляем файловый обработчик
    file_handler = logging.FileHandler("radius_core.log")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Настройки для сторонних библиотек
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("aioredis").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)

    return root_logger


def log_with_context(logger: logging.Logger, level: str, message: str, **context):
    """Логирование с контекстом"""
    # Создаем LogRecord с дополнительным контекстом
    record = logger.makeRecord(
        logger.name, getattr(logging, level.upper()), "", 0, message, (), None
    )

    # Добавляем контекст
    for key, value in context.items():
        setattr(record, key, value)

    logger.handle(record)


# Создаем специализированные логгеры
radius_logger = logging.getLogger("radius")
metrics_logger = logging.getLogger("metrics")
redis_logger = logging.getLogger("redis")
rabbitmq_logger = logging.getLogger("rabbitmq")


def log(level: str, message: str, **context):
    """Универсальная функция логирования"""
    log_with_context(radius_logger, level, message, **context)
