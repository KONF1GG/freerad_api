"""Утилиты для работы с интервалами услуг."""

import logging
import time
from typing import Optional, Tuple

from ..models.schemas import (
    ServiceCats,
    ServiceCategory,
    ServiceInterval,
    LoginSearchResult,
)

logger = logging.getLogger(__name__)


def get_active_interval(
    service_category: ServiceCategory, current_time: Optional[float] = None
) -> Optional[ServiceInterval]:
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
    servicecats: Optional[ServiceCats], current_time: Optional[float] = None
) -> Tuple[Optional[str], Optional[ServiceInterval], Optional[ServiceCategory]]:
    """
    Определяет услугу с наивысшим приоритетом среди активных.
    Проверяет все категории и выбирает только те, у которых name начинается с "INET".

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

    # Получаем все сервисы (включая динамически добавленные)
    all_services = servicecats.get_all_services()

    for field_name, service_category in all_services.items():
        # Проверяем наличие поля name и что оно начинается с "INET"
        service_name_field = getattr(service_category, "name", None)

        # Для поля "internet" если name отсутствует - используем дефолтное имя
        if field_name == "internet" and not service_name_field:
            service_name_field = "INET-FREEDOM"

        if not service_name_field or not service_name_field.startswith("INET"):
            continue

        # Проверяем есть ли активный интервал
        active_interval = get_active_interval(service_category, current_time)
        if not active_interval:
            continue

        priority = service_category.prio
        active_services.append(
            (priority, field_name, active_interval, service_category)
        )

    if not active_services:
        return None, None, None

    # Сортируем по приоритету (возрастание): меньшее число = выше приоритет
    # Например: prio=1 > prio=10
    active_services.sort(key=lambda x: x[0])
    _, service_name, active_interval, service_category = active_services[0]

    return service_name, active_interval, service_category


def get_effective_speed_params(
    servicecats: Optional[ServiceCats], current_time: Optional[float] = None
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

    service_name, active_interval, service_category = (
        get_highest_priority_active_service(servicecats, current_time)
    )

    if not service_name or not active_interval:
        return None, None, None

    speed = active_interval.speed
    speed_night = active_interval.speed_night

    # contype берется из категории (может быть None)
    contype = getattr(service_category, "contype", None)

    return speed, speed_night, contype


def check_service_should_be_blocked(
    servicecats: Optional[ServiceCats], current_time: Optional[float] = None
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
    service_name, active_interval, service_category = (
        get_highest_priority_active_service(servicecats, current_time)
    )

    # Если нет активных услуг - блокируем
    if not service_name:
        return True

    # Для всех остальных активных услуг - не блокируем
    return False


def get_service_params_for_login(
    login_data: LoginSearchResult,
) -> Tuple[Optional[int], Optional[int], Optional[str], bool, Optional[str]]:
    """
    Извлекает параметры услуги из данных логина.

    Args:
        login_data: Данные логина

    Returns:
        Кортеж (speed, speed_night, contype, should_be_blocked, service_name)
        service_name - имя активного сервиса (например "INET-TURBO", "INET-FREEDOM")
    """
    servicecats = getattr(login_data, "servicecats", None)
    override_speed = getattr(login_data, "speed", None)
    override_speed_night = getattr(login_data, "speed_night", None)
    if not servicecats:
        # Нет servicecats - блокируем
        return None, None, None, True, None

    # Проверяем блокировку
    should_be_blocked = check_service_should_be_blocked(servicecats)
    if should_be_blocked:
        return None, None, None, True, None

    service_name_field, active_interval, active_service_category = (
        get_highest_priority_active_service(servicecats)
    )

    if not service_name_field or not active_interval:
        return None, None, None, True, None

    # Получаем имя сервиса из категории
    service_name = (
        getattr(active_service_category, "name", None)
        if active_service_category
        else None
    )
    speed = active_interval.speed if override_speed == "0" or None else override_speed
    speed_night = active_interval.speed_night if override_speed_night == "0" or None else override_speed_night

    # contype берется из категории (может быть None)
    contype = getattr(active_service_category, "contype", None)

    return speed, speed_night, contype, False, service_name


def is_iptv_enabled(
    servicecats: Optional[ServiceCats], current_time: Optional[float] = None
) -> bool:
    """
    Проверяет, включено ли IPTV в активном интервале.

    Args:
        servicecats: Категории услуг
        current_time: Текущее время (timestamp)

    Returns:
        True если IPTV включено
    """
    if not servicecats:
        return False

    service_name, active_interval, service_category = (
        get_highest_priority_active_service(servicecats, current_time)
    )

    # IPTV может быть включено в любом активном интервале
    if active_interval:
        return getattr(active_interval, "iptv", False)

    return False
