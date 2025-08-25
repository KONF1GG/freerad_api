"""
Конфигурация приложения.
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# Redis настройки с оптимизацией производительности
REDIS_URL: str = os.getenv("REDIS_URL", "")
REDIS_POOL_SIZE: int = int(
    os.getenv("REDIS_POOL_SIZE", "100")
)  # Увеличиваем размер пула
REDIS_CONCURRENCY: int = int(
    os.getenv(
        "REDIS_CONCURRENCY", str(max(1, int(REDIS_POOL_SIZE * 0.9)))
    )  # Увеличиваем параллельность
)

REDIS_COMMAND_TIMEOUT: float = float(
    os.getenv("REDIS_COMMAND_TIMEOUT", "1.0")
)  # Уменьшаем timeout
REDIS_WARNING_THRESHOLD: int = int(
    os.getenv("REDIS_WARNING_THRESHOLD", "50")
)  # Увеличиваем порог

# RabbitMQ настройки с оптимизацией
AMQP_URL: str = os.getenv("AMQP_URL", "")
AMQP_EXCHANGE: str = os.getenv("AMQP_EXCHANGE", "sessions_traffic_exchange")
AMQP_SESSION_QUEUE: str = os.getenv("AMQP_SESSION_QUEUE", "session_queue")
AMQP_TRAFFIC_QUEUE: str = os.getenv("AMQP_TRAFFIC_QUEUE", "traffic_queue")
AMQP_AUTH_LOG_QUEUE: str = os.getenv("AMQP_AUTH_LOG_QUEUE", "auth_log_queue")
AMQP_COA_QUEUE: str = os.getenv("AMQP_COA_QUEUE", "coa_requests")

# Настройки производительности
WORKER_CONCURRENCY: int = int(
    os.getenv("WORKER_CONCURRENCY", "4")
)  # Количество воркеров
MAX_CONCURRENT_REQUESTS: int = int(
    os.getenv("MAX_CONCURRENT_REQUESTS", "1000")
)  # Максимум одновременных запросов

RADIUS_SESSION_PREFIX: str = "radius:session:"
RADIUS_LOGIN_PREFIX: str = "login:"
RADIUS_INDEX_NAME: str = "idx:radius:login"

PROMETHEUS_MULTIPROC_DIR: str = os.getenv(
    "PROMETHEUS_MULTIPROC_DIR", "/app/prometheus_multiproc_dir"
)
