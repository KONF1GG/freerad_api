"""RADIUS Core - Основной пакет для обработки RADIUS данных."""

__version__ = "1.0.0"
__author__ = "RADIUS Core Team"

# Импорты основных компонентов
from .core import create_app

# Сервисы
from .services import (
    auth,
    process_accounting,
    check_and_correct_services,
    get_session_from_redis,
    find_login_by_session,
    find_sessions_by_login,
    send_to_session_queue,
    send_to_traffic_queue,
    process_traffic_data,
    send_auth_log_to_queue,
    save_session_to_redis,
    delete_session_from_redis,
    update_main_session_service,
    enrich_session_with_login,
    send_coa_to_queue,
    send_coa_session_kill,
    send_coa_session_set,
    check_and_correct_service_state,
)

# Модели
from .models import (
    AccountingData,
    AccountingResponse,
    AuthRequest,
    AuthResponse,
    SessionData,
    TrafficData,
    EnrichedSessionData,
    LoginSearchResult,
    VideoLoginSearchResult,
    SessionsSearchRequest,
    SessionsSearchResponse,
    AuthDataLog,
    RABBIT_MODELS,
)

# Клиенты
from .clients import (
    redis_health_check,
    rabbitmq_health_check,
)

# Утилиты
from .utils import (
    nasportid_parse,
    is_username_mac,
    mac_from_username,
    username_from_mac,
    mac_from_hex,
    now_str,
    parse_event,
)

__all__ = [
    # Основные компоненты
    "create_app",
    # Сервисы
    "auth",
    "process_accounting",
    "check_and_correct_services",
    "get_session_from_redis",
    "find_login_by_session",
    "find_sessions_by_login",
    "send_to_session_queue",
    "send_to_traffic_queue",
    "process_traffic_data",
    "send_auth_log_to_queue",
    "save_session_to_redis",
    "delete_session_from_redis",
    "update_main_session_service",
    "enrich_session_with_login",
    "send_coa_to_queue",
    "send_coa_session_kill",
    "send_coa_session_set",
    "check_and_correct_service_state",
    # Модели
    "AccountingData",
    "AccountingResponse",
    "AuthRequest",
    "AuthResponse",
    "SessionData",
    "TrafficData",
    "EnrichedSessionData",
    "LoginSearchResult",
    "VideoLoginSearchResult",
    "SessionsSearchRequest",
    "SessionsSearchResponse",
    "AuthDataLog",
    "RABBIT_MODELS",
    # Клиенты
    "redis_health_check",
    "rabbitmq_health_check",
    # Утилиты
    "nasportid_parse",
    "is_username_mac",
    "mac_from_username",
    "username_from_mac",
    "mac_from_hex",
    "now_str",
    "parse_event",
]
