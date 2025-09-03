"""Сервисы для обработки данных RADIUS."""

from .coa import (
    send_coa_to_queue,
    send_coa_session_kill,
    send_coa_session_set,
)
from .auth import (
    auth,
    find_duplicate_sessions_by_username_vlan,
    find_duplicate_sessions_by_onu_mac,
    find_device_sessions_by_device_data,
    kill_duplicate_sessions,
)
from .accounting import process_accounting
from .monitoring import (
    check_and_correct_service_state,
    check_and_correct_services,
)
from .storage import (
    get_session_from_redis,
    find_login_by_session,
    find_sessions_by_login,
    send_to_session_queue,
    send_to_traffic_queue,
    send_auth_log_to_queue,
    save_session_to_redis,
    delete_session_from_redis,
    update_main_session_service,
)
from .data_processing import (
    enrich_session_with_login,
    process_traffic_data,
)

__all__ = [
    # CoA операции
    "send_coa_to_queue",
    "send_coa_session_kill",
    "send_coa_session_set",
    # Авторизация
    "auth",
    # Аккаунтинг
    "process_accounting",
    # Проверка сервисов
    "check_and_correct_service_state",
    "check_and_correct_services",
    # Обработка дублирующих сессий
    "find_duplicate_sessions_by_username_vlan",
    "find_duplicate_sessions_by_onu_mac",
    "find_device_sessions_by_device_data",
    "kill_duplicate_sessions",
    # CRUD операции
    "get_session_from_redis",
    "enrich_session_with_login",
    "find_login_by_session",
    "find_sessions_by_login",
    "send_to_session_queue",
    "send_to_traffic_queue",
    "process_traffic_data",
    "send_auth_log_to_queue",
    "save_session_to_redis",
    "delete_session_from_redis",
    "update_main_session_service",
]
