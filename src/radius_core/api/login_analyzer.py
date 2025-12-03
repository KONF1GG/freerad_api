"""
Роут для анализа реальных логинов из Redis и проверки новой логики услуг.
"""

import time
import logging
import json
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends

from ..models.schemas import LoginSearchResult
from ..core.dependencies import RedisDependency
from ..config import RADIUS_LOGIN_PREFIX
from ..utils.service_intervals import (
    get_service_params_for_login,
    is_iptv_enabled,
    get_highest_priority_active_service,
    get_effective_speed_params,
    check_service_should_be_blocked,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/analyze_login/{login}")
async def analyze_login(login: str, redis: RedisDependency) -> Dict[str, Any]:
    """
    Анализирует реальный логин из Redis и проверяет новую логику услуг.

    Args:
        login: Логин пользователя для получения из Redis
        redis: Redis клиент

    Returns:
        Полная информация об услугах, приоритетах и финальных параметрах
    """
    logger.info("Анализ реального логина из Redis: %s", login)

    try:
        login_key = f"{RADIUS_LOGIN_PREFIX}{login}"
        login_data_raw = await redis.execute_command("JSON.GET", login_key)

        if not login_data_raw:
            raise HTTPException(
                status_code=404, detail=f"Логин {login} не найден в Redis"
            )

        # Парсим данные логина
        login_data_dict = json.loads(login_data_raw)
        login_data = LoginSearchResult.model_validate(login_data_dict)

        # Текущее время
        current_time = time.time()

        # Базовая информация
        result = {
            "login": login,
            "redis_key": login_key,
            "analysis_time": current_time,
            "analysis_datetime": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(current_time)
            ),
            "raw_data": login_data_dict,
        }

        # Анализ servicecats
        servicecats = getattr(login_data, "servicecats", None)
        iptv_enabled = False

        if servicecats:
            servicecats_analysis = _analyze_servicecats(servicecats, current_time)
            iptv_enabled = bool(servicecats_analysis.get("iptv_enabled", False))
            result["servicecats_analysis"] = servicecats_analysis
        else:
            result["servicecats_analysis"] = {
                "has_servicecats": False,
                "legacy_speed_field": getattr(login_data, "speed", None),
                "message": "No servicecats found - will be blocked",
            }

        # Основные параметры через новую логику
        speed, speed_night, contype, should_be_blocked, service_name = (
            get_service_params_for_login(login_data)
        )

        final_speed = _safe_int(speed, "speed")
        final_speed_night = _safe_int(speed_night, "speed_night")

        result["new_logic_results"] = {
            "speed": final_speed,
            "speed_night": final_speed_night,
            "contype": contype,
            "should_be_blocked": should_be_blocked,
            "service_name": service_name,
            "iptv_enabled": iptv_enabled,
        }

        # Скорость берется напрямую из интервалов

        result["radius_config"] = {
            "final_speed_mbps": final_speed,
            "final_speed_night_mbps": final_speed_night,
            "final_speed_kb": _mbps_to_kbps(final_speed),
            "service_activation": _get_service_activation_string(
                final_speed, contype, should_be_blocked, service_name
            ),
            "should_be_blocked": should_be_blocked,
            "note": "Speed is taken directly from intervals (turbo already included)",
        }

        # Сравнение со старой логикой (legacy speed field)
        legacy_speed = getattr(login_data, "speed", None)
        legacy_speed_int = _safe_int(legacy_speed, "legacy_speed")
        if legacy_speed_int and legacy_speed_int != 0:
            legacy_speed_kb = _mbps_to_kbps(legacy_speed_int)

            result["legacy_comparison"] = {
                "legacy_speed_mbps": legacy_speed_int,
                "legacy_speed_kb": legacy_speed_kb,
                "legacy_activation": f"INET-FREEDOM({legacy_speed_kb}k)",
                "speed_changed": (
                    final_speed != legacy_speed_int if final_speed is not None else True
                ),
                "new_vs_legacy": {
                    "new_speed_from_intervals": final_speed,
                    "old_legacy_speed_field": legacy_speed_int,
                    "difference": (
                        final_speed - legacy_speed_int
                        if final_speed is not None
                        else None
                    ),
                },
            }

        return result

    except json.JSONDecodeError as e:
        logger.error("Ошибка парсинга JSON для логина %s: %s", login, e)
        raise HTTPException(
            status_code=500, detail=f"Некорректные данные в Redis: {e}"
        ) from e
    except HTTPException:
        # Пробрасываем HTTP исключения (например 404), чтобы они не оборачивались в 500
        raise
    except Exception as e:
        logger.error("Ошибка анализа логина %s: %s", login, e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Ошибка анализа логина: {e}"
        ) from e


def _analyze_servicecats(servicecats, current_time: float) -> Dict[str, Any]:
    """Анализирует servicecats согласно новой логике."""
    if not servicecats:
        return {"has_servicecats": False, "message": "servicecats отсутствует"}

    services: Dict[str, Any] = {}

    # Динамически проходимся по всем полям servicecats
    for field_name in dir(servicecats):
        # Пропускаем служебные поля
        if field_name.startswith("_") or field_name in ["model_config", "model_fields"]:
            continue

        service = getattr(servicecats, field_name, None)

        # Проверяем что это объект категории услуги
        if not service or not hasattr(service, "intervals"):
            continue

        intervals = []
        is_active_now = False
        if getattr(service, "intervals", None):
            intervals = [
                {
                    "begin": interval.begin,
                    "end": interval.end,
                    "speed": interval.speed,
                    "speed_night": interval.speed_night,
                    "has_turbo": getattr(interval, "has_turbo", None),
                    "turbo": getattr(interval, "turbo", None),
                }
                for interval in service.intervals
            ]
            is_active_now = any(
                interval.begin <= current_time <= interval.end
                for interval in service.intervals
            )

        services[field_name] = {
            "name": getattr(service, "name", None),
            "prio": getattr(service, "prio", None),
            "base_speed_legacy": getattr(service, "speed", None),  # Not used anymore
            "base_speed_night_legacy": getattr(
                service, "speed_night", None
            ),  # Not used anymore
            "contype": getattr(service, "contype", None),
            "intervals": intervals,
            "active_now": is_active_now,
        }

    highest_service, active_interval, service_category = (
        get_highest_priority_active_service(servicecats, current_time)
    )

    should_be_blocked = check_service_should_be_blocked(servicecats, current_time)
    speed, speed_night, contype = get_effective_speed_params(servicecats, current_time)
    iptv_enabled = is_iptv_enabled(servicecats, current_time)

    active_interval_dict: Optional[Dict[str, Any]] = None
    if active_interval:
        active_interval_dict = {
            "begin": active_interval.begin,
            "end": active_interval.end,
            "speed": active_interval.speed,
            "speed_night": active_interval.speed_night,
            "has_turbo": getattr(active_interval, "has_turbo", None),
            "turbo": getattr(active_interval, "turbo", None),
            "iptv": getattr(active_interval, "iptv", None),
        }

    return {
        "has_servicecats": True,
        "services": services,
        "effective_speed_from_intervals": {
            "speed": _safe_int(speed, "interval.speed"),
            "speed_night": _safe_int(speed_night, "interval.speed_night"),
            "contype": contype,
            "note": "Speed taken ONLY from active interval (turbo already included if active)",
        },
        "should_be_blocked": should_be_blocked,
        "iptv_enabled": iptv_enabled,
        "highest_priority_active_service": {
            "name": highest_service,
            "prio": getattr(service_category, "prio", None)
            if service_category
            else None,
            "contype": getattr(service_category, "contype", None)
            if service_category
            else None,
            "active_interval": active_interval_dict,
        },
    }


def _get_service_activation_string(
    speed: Optional[int],
    contype: Optional[str],
    should_be_blocked: bool,
    service_name: Optional[str] = None,
) -> str:
    """Генерирует строку активации услуги для RADIUS."""
    if should_be_blocked:
        return "NOINET-NOMONEY()"

    if not speed:
        return "NOINET-NOMONEY()"

    # Используем имя сервиса из активного интервала
    if contype == "social":
        service_name = service_name or "INET-SOCIAL"
        return f"{service_name}()"

    service_name = service_name or "INET-FREEDOM"
    speed_kb = int(speed * 1100)
    return f"{service_name}({speed_kb}k)"


def _safe_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None

    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid speed value")
        return int(float(value))
    except (TypeError, ValueError):
        logger.warning("Не удалось преобразовать %s=%r к int", field_name, value)
        return None


def _safe_float(value: Any, field_name: str) -> Optional[float]:
    if value is None:
        return None

    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid numeric value")
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Не удалось преобразовать %s=%r к float", field_name, value)
        return None


def _mbps_to_kbps(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None

    return int(value * 1100)
