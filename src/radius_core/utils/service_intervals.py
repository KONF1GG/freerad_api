"""Утилиты для работы с интервалами услуг."""

import logging
import time
from typing import Dict, List, Optional, Tuple, Any

from ..models.schemas import ServiceCats, ServiceCategory, ServiceInterval, LoginSearchResult

logger = logging.getLogger(__name__)

# Fallback приоритеты, если prio не указан в данных
SERVICE_PRIORITIES = {
    "testdrive": 4,
    "gifts": 3, 
    "turbo": 2,
    "internet": 1,
    "children": 1,
    "stopped": 1,
}


def get_active_interval(service_category: ServiceCategory, current_time: Optional[float] = None) -> Optional[ServiceInterval]:
    """
    Получает активный интервал для категории услуги.
    
    Args:
        service_category: Категория услуги
        current_time: Текущее время (timestamp), если None - используется time.time()
    
    Returns:
        Активный интервал или None
    """
    if not service_category or not service_category.intervals:
        return None
    
    if current_time is None:
        current_time = time.time()
    
    for interval in service_category.intervals:
        if interval.begin <= current_time <= interval.end:
            return interval
    
    return None


def get_highest_priority_active_service(
    servicecats: ServiceCats, 
    current_time: Optional[float] = None
) -> Tuple[Optional[str], Optional[ServiceInterval], Optional[ServiceCategory]]:
    """
    Определяет услугу с наивысшим приоритетом среди активных.
    
    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)
    
    Returns:
        Кортеж (название_категории, активный_интервал, категория_услуги)
    """
    if not servicecats:
        return None, None, None
    
    if current_time is None:
        current_time = time.time()
    
    active_services = []
    
    # Проверяем все категории услуг
    for service_name in ["testdrive", "gifts", "turbo", "internet", "children", "stopped"]:
        service_category = getattr(servicecats, service_name, None)
        if not service_category:
            continue
            
        active_interval = get_active_interval(service_category, current_time)
        if active_interval:
            # Используем приоритет из поля prio, если задан, иначе fallback к статическому
            priority = getattr(service_category, "prio", None)
            if priority is None:
                priority = SERVICE_PRIORITIES.get(service_name, 0)
            active_services.append((priority, service_name, active_interval, service_category))
    
    if not active_services:
        return None, None, None
    
    # Сортируем по приоритету (убывание) и берем первый
    active_services.sort(key=lambda x: x[0], reverse=True)
    _, service_name, active_interval, service_category = active_services[0]
    
    return service_name, active_interval, service_category


def get_effective_speed_params(
    servicecats: ServiceCats, 
    current_time: Optional[float] = None
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Получает эффективные параметры скорости с учетом приоритетов.
    
    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)
    
    Returns:
        Кортеж (speed, speed_night, contype)
    """
    if not servicecats:
        return None, None, None

    service_name, active_interval, service_category = get_highest_priority_active_service(
        servicecats, current_time
    )

    if not service_name:
        return None, None, None

    # Для testdrive и gifts - параметры переопределяются значениями из интервала
    if service_name in ["testdrive", "gifts"]:
        return (
            active_interval.speed if active_interval else None,
            active_interval.speed_night if active_interval else None,
            None  # contype для testdrive/gifts не применим
        )

    # Для остальных услуг - берем из интервала, если есть, иначе из базовой категории
    speed = None
    speed_night = None
    contype = getattr(service_category, "contype", None)

    if active_interval:
        speed = active_interval.speed
        speed_night = active_interval.speed_night

    # Если в интервале нет значений, берем из базовой категории
    if speed is None:
        speed = getattr(service_category, "speed", None)
    if speed_night is None:
        speed_night = getattr(service_category, "speed_night", None)

    return speed, speed_night, contype


def check_service_should_be_blocked(
    servicecats: ServiceCats, 
    current_time: Optional[float] = None
) -> bool:
    """
    Проверяет, должна ли услуга быть заблокирована.
    
    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)
    
    Returns:
        True если услуга должна быть заблокирована
    """
    if not servicecats:
        return True
    
    if current_time is None:
        current_time = time.time()
    
    # Сначала проверяем, есть ли активная услуга "stopped" - она блокирует в любом случае
    stopped_category = getattr(servicecats, "stopped", None)
    if stopped_category:
        stopped_interval = get_active_interval(stopped_category, current_time)
        if stopped_interval:
            return True
    
    # Затем получаем услугу с наивысшим приоритетом среди всех активных
    service_name, active_interval, service_category = get_highest_priority_active_service(
        servicecats, current_time
    )
    
    # Если нет активных услуг - блокируем
    if not service_name:
        return True
    
    # Для всех остальных активных услуг - не блокируем
    return False


def get_service_params_for_login(login_data: LoginSearchResult) -> Tuple[Optional[int], Optional[int], Optional[str], bool]:
    """
    Извлекает параметры услуги из данных логина.
    
    Args:
        login_data: Данные логина
    
    Returns:
        Кортеж (speed, speed_night, contype, should_be_blocked)
    """
    servicecats = getattr(login_data, "servicecats", None)
    if not servicecats:
        # Fallback к старой логике
        legacy_speed = getattr(login_data, "speed", None)
        if legacy_speed and legacy_speed != "0":
            return int(legacy_speed), None, None, False
        return None, None, None, True

    # Проверяем блокировку
    should_be_blocked = check_service_should_be_blocked(servicecats)
    if should_be_blocked:
        return None, None, None, True

    # Получаем эффективные параметры
    speed, speed_night, contype = get_effective_speed_params(servicecats)

    # Fallback к старому полю speed если нет в servicecats
    if speed is None:
        legacy_speed = getattr(login_data, "speed", None)
        if legacy_speed and legacy_speed != "0":
            speed = int(legacy_speed)

    return speed, speed_night, contype, False


def get_turbo_multiplier(servicecats: ServiceCats, current_time: Optional[float] = None) -> Optional[int]:
    """
    Получает множитель турбо режима, если он активен.
    
    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)
    
    Returns:
        Множитель турбо или None
    """
    if not servicecats:
        return None
    
    service_name, active_interval, service_category = get_highest_priority_active_service(
        servicecats, current_time
    )
    
    # Турбо активен если либо активна услуга turbo, либо в интервале internet есть has_turbo=True
    if service_name == "turbo":
        return getattr(active_interval, "turbo", 1) if active_interval else 1
    elif service_name == "internet" and active_interval and getattr(active_interval, "has_turbo", False):
        return getattr(active_interval, "turbo", 1)
    
    return None


def is_iptv_enabled(servicecats: ServiceCats, current_time: Optional[float] = None) -> bool:
    """
    Проверяет, включено ли IPTV.
    
    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)
    
    Returns:
        True если IPTV включено
    """
    if not servicecats:
        return False
    
    service_name, active_interval, service_category = get_highest_priority_active_service(
        servicecats, current_time
    )
    
    # IPTV может быть включено в testdrive или gifts
    if service_name in ["testdrive", "gifts"] and active_interval:
        return getattr(active_interval, "iptv", False)
    
    return False