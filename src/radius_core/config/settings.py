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

SESSION_LIMIT: int = 15

PROMETHEUS_MULTIPROC_DIR: str = os.getenv(
    "PROMETHEUS_MULTIPROC_DIR", "/app/prometheus_multiproc_dir"
)

# Kafka
KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
KAFKA_ACCOUNTING_TOPIC: str = os.getenv("KAFKA_ACCOUNTING_TOPIC", "radius.accounting")
KAFKA_TEST_TOPIC: str = os.getenv("KAFKA_TEST_TOPIC", "radius.test")
KAFKA_CLIENT_ID: str = os.getenv("KAFKA_CLIENT_ID", "radius-core")
KAFKA_ACKS: str = os.getenv("KAFKA_ACKS", "all")
KAFKA_LINGER_MS: int = int(os.getenv("KAFKA_LINGER_MS", "5"))
KAFKA_COMPRESSION_TYPE: str = os.getenv("KAFKA_COMPRESSION_TYPE", "gzip")
KAFKA_REQUEST_TIMEOUT_MS: int = int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS", "30000"))
KAFKA_MAX_REQUEST_SIZE: int = int(os.getenv("KAFKA_MAX_REQUEST_SIZE", "1048576"))

# Kafka security (SASL/TLS)
KAFKA_SECURITY_PROTOCOL: str = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_MECHANISM: str = os.getenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
KAFKA_SASL_USERNAME: str = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD: str = os.getenv("KAFKA_SASL_PASSWORD", "")
KAFKA_SSL_CAFILE: str = os.getenv("KAFKA_SSL_CAFILE", "")
KAFKA_SSL_CERTFILE: str = os.getenv("KAFKA_SSL_CERTFILE", "")
KAFKA_SSL_KEYFILE: str = os.getenv("KAFKA_SSL_KEYFILE", "")
KAFKA_SSL_CHECK_HOSTNAME: bool = (
    os.getenv("KAFKA_SSL_CHECK_HOSTNAME", "true").lower() == "true"
)
