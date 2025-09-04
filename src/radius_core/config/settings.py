"""
Конфигурация приложения.
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# Настройки логирования
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
EXTERNAL_LOG_LEVEL: str = os.getenv("EXTERNAL_LOG_LEVEL", "WARNING")
LOG_FILE: str = os.getenv("LOG_FILE", "/var/log/radius_core/radius_log.log")
LOG_CONSOLE: str = os.getenv("LOG_CONSOLE", "true")

REDIS_URL: str = os.getenv("REDIS_URL", "")
REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "50"))
REDIS_CONCURRENCY: int = int(
    os.getenv("REDIS_CONCURRENCY", str(max(1, int(REDIS_POOL_SIZE * 0.8))))
)

REDIS_COMMAND_TIMEOUT: float = float(os.getenv("REDIS_COMMAND_TIMEOUT", "3.0"))
REDIS_WARNING_THRESHOLD: int = int(os.getenv("REDIS_WARNING_THRESHOLD", "20"))
AMQP_URL: str = os.getenv("AMQP_URL", "")
AMQP_EXCHANGE: str = os.getenv("AMQP_EXCHANGE", "sessions_traffic_exchange")
AMQP_SESSION_QUEUE: str = os.getenv("AMQP_SESSION_QUEUE", "session_queue")
AMQP_TRAFFIC_QUEUE: str = os.getenv("AMQP_TRAFFIC_QUEUE", "traffic_queue")
AMQP_AUTH_LOG_QUEUE: str = os.getenv("AMQP_AUTH_LOG_QUEUE", "auth_log_queue")
AMQP_COA_QUEUE: str = os.getenv("AMQP_COA_QUEUE", "coa_requests")
AMQP_COA_EXCHANGE: str = os.getenv("AMQP_COA_EXCHANGE", "coa_exchange")
AMQP_DLQ_EXCHANGE: str = os.getenv("AMQP_DLQ_EXCHANGE", "coa_dlq_exchange")
AMQP_DLQ_QUEUE: str = os.getenv("AMQP_DLQ_QUEUE", "dlq_coa_requests")


RADIUS_SESSION_PREFIX: str = "radius:session:"
RADIUS_LOGIN_PREFIX: str = "login:"
RADIUS_INDEX_NAME: str = "idx:radius:login"
RADIUS_INDEX_NAME_SESSION: str = "idx:radius:session"

PROMETHEUS_MULTIPROC_DIR: str = os.getenv(
    "PROMETHEUS_MULTIPROC_DIR", "/app/prometheus_multiproc_dir"
)
