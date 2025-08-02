import os

# Redis Configuration
REDIS_URL: str = os.getenv(
    "REDIS_URL", "redis://default:qwertfdsa@dev2.freedom1.ru:6379/0"
)
REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "10"))

# RabbitMQ Configuration
AMQP_URL: str = os.getenv("AMQP_URL", "amqp://leo:12345@192.168.110.52:5672/")
AMQP_EXCHANGE: str = os.getenv("AMQP_EXCHANGE", "sessions_traffic_exchange_test")
AMQP_SESSION_QUEUE: str = os.getenv("AMQP_SESSION_QUEUE", "session_queue_test")
AMQP_TRAFFIC_QUEUE: str = os.getenv("AMQP_TRAFFIC_QUEUE", "traffic_queue_test")

# Radius Configuration
RADIUS_SESSION_PREFIX: str = "radius:session:"
RADIUS_LOGIN_PREFIX: str = "login:"
RADIUS_INDEX_NAME: str = "idx:radius:login"

# Session Template
SESSION_TEMPLATE = {
    "login": "",
    "auth_type": "UNKNOWN",
    "contract": "",
    "onu_mac": "",
    "service": "",
    "Acct-Start-Time": 0,
    "Acct-Session-Time": 0,
    "Acct-Input-Octets": 0,
    "Acct-Output-Octets": 0,
    "Acct-Input-Gigawords": 0,
    "Acct-Output-Gigawords": 0,
}
