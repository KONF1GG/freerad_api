"""Утилиты для работы с сервисами RADIUS."""

import logging
from typing import Any

logger = logging.getLogger("radius_core")


def check_service_expiry(timeto: Any, now_timestamp: float) -> bool:
    """
    Проверяет истек ли срок действия услуги

    Args:
        timeto: Время истечения услуги
        now_timestamp: Текущее время в timestamp

    Returns:
        bool: True если услуга истекла, False если активна
    """
    if timeto is None:
        return True

    try:
        timeto_float = float(timeto)
        return timeto_float < now_timestamp
    except (ValueError, TypeError) as e:
        logger.error("Некорректное значение timeto: %s, ошибка: %s", timeto, e)
        return True
