"""
Роут для анализа реальных логинов из Redis и проверки новой логики услуг.
"""

import time
import logging
import json
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException

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
    """Простой и наглядный анализ логина с приоритетами услуг."""
    logger.info("Анализ логина: %s", login)

    try:
        login_key = f"{RADIUS_LOGIN_PREFIX}{login}"
        login_data_raw = await redis.execute_command("JSON.GET", login_key)

        if not login_data_raw:
            raise HTTPException(
                status_code=404, detail=f"Логин {login} не найден в Redis"
            )

        login_data_dict = json.loads(login_data_raw)
        login_data = LoginSearchResult.model_validate(login_data_dict)
        current_time = time.time()

        # Базовая информация
        result = {
            "🔍 АНАЛИЗ ЛОГИНА": login,
            "⏰ Время анализа": _format_time(current_time),
            "📊 Timestamp": current_time,
        }

        servicecats = getattr(login_data, "servicecats", None)
        if not servicecats:
            result["🚫 СТАТУС"] = "БЛОКИРОВКА - НЕТ УСЛУГ"
            result["💡 Причина"] = "Отсутствуют servicecats"
            return result

        # Анализ всех услуг
        all_services = servicecats.get_all_services()
        services_info = []

        for field_name, service in all_services.items():
            if not service or not getattr(service, "intervals", None):
                continue

            service_name = getattr(service, "name", None)
            priority = getattr(service, "prio", None)

            # Проверяем каждый интервал
            intervals_info = []
            has_active_interval = False

            for interval in service.intervals:
                is_active = interval.begin <= current_time <= interval.end
                if is_active:
                    has_active_interval = True

                interval_info = {
                    "📝 Название": getattr(interval, "name", "Без названия"),
                    "🕐 Начало": _format_time(interval.begin),
                    "🕑 Конец": _format_time(interval.end),
                    "📡 Статус": "✅ АКТИВЕН" if is_active else "❌ неактивен",
                    "⚡ Скорость": f"{interval.speed} Мбит/с"
                    if interval.speed
                    else "не установлена",
                    "🌙 Ночная скорость": f"{interval.speed_night} Мбит/с"
                    if interval.speed_night
                    else "не установлена",
                }

                # Информация о турбо
                has_turbo = getattr(interval, "has_turbo", False)
                turbo_multiplier = getattr(interval, "turbo", 1)
                if has_turbo and turbo_multiplier and turbo_multiplier > 1:
                    interval_info["🚀 Турбо"] = f"x{turbo_multiplier}"

                intervals_info.append(interval_info)

            # Проверяем, является ли это INET-сервисом
            is_inet_service = (
                service_name and service_name.startswith("INET")
                if service_name
                else field_name == "internet"
            )
            if field_name == "internet" and not service_name:
                service_name = "INET-FREEDOM"

            # Создаем базовую информацию о сервисе
            service_info = {
                "🏷️ Поле": field_name,
                "📋 Название": service_name,
                "🎯 Приоритет": priority,
                "🌐 Тип": "🌐 INET-сервис" if is_inet_service else "⚪ Другая услуга",
                "📊 Активность": "✅ АКТИВНА"
                if has_active_interval and is_inet_service
                else "❌ неактивна",
                "📅 Интервалы": intervals_info,
            }

            # Добавляем причину неактивности только для неактивных сервисов
            if not (has_active_interval and is_inet_service):
                service_info["💭 Причина неактивности"] = _get_inactive_reason(
                    service, current_time, is_inet_service
                )

            services_info.append(service_info)

        # Сортируем услуги по приоритету для наглядности
        services_info.sort(
            key=lambda x: x["🎯 Приоритет"] if x["🎯 Приоритет"] is not None else 999
        )

        result["📋 ВСЕ УСЛУГИ"] = services_info

        # Определяем финальный результат
        service_name, active_interval, service_category, computed_service_name = (
            get_highest_priority_active_service(servicecats, current_time)
        )

        # Проверка на блокировку
        should_be_blocked = check_service_should_be_blocked(servicecats, current_time)

        if should_be_blocked:
            # Проверяем причину блокировки
            stopped_service = getattr(servicecats, "stopped", None)
            if stopped_service:
                stopped_interval = get_active_interval(stopped_service, current_time)
                if stopped_interval:
                    result["🚫 СТАТУС"] = "ПРИНУДИТЕЛЬНАЯ БЛОКИРОВКА"
                    result["💡 Причина"] = "Активна услуга 'stopped'"
                    return result

            result["🚫 СТАТУС"] = "БЛОКИРОВКА"
            result["💡 Причина"] = "Нет активных INET-услуг"
            return result

        # Есть активная услуга
        result["🎯 ВЫБРАННАЯ УСЛУГА"] = {
            "📋 Название": computed_service_name,
            "🏷️ Поле": service_name,
            "🎯 Приоритет": getattr(service_category, "prio", None)
            if service_category
            else None,
            "💭 Причина выбора": f"Наивысший приоритет ({getattr(service_category, 'prio', None)}) среди активных INET-сервисов",
        }

        if active_interval:
            result["🎯 ВЫБРАННАЯ УСЛУГА"]["📅 Активный интервал"] = {
                "📝 Название": getattr(active_interval, "name", "Без названия"),
                "🕐 Начало": _format_time(active_interval.begin),
                "🕑 Конец": _format_time(active_interval.end),
                "⚡ Скорость": f"{active_interval.speed} Мбит/с",
                "🌙 Ночная скорость": f"{active_interval.speed_night} Мбит/с",
            }

        # Финальная конфигурация
        speed, speed_night, contype, _, service_name_final = (
            get_service_params_for_login(login_data, current_time)
        )

        result["⚡ ИТОГОВЫЕ ПАРАМЕТРЫ"] = {
            "🚀 Скорость": f"{speed} Мбит/с" if speed else "не установлена",
            "🌙 Ночная скорость": f"{speed_night} Мбит/с"
            if speed_night
            else "не установлена",
            "🔌 Тип подключения": contype or "не указан",
            "📺 IPTV": "✅ включено"
            if is_iptv_enabled(servicecats, current_time)
            else "❌ отключено",
            "📡 RADIUS команда": _get_service_activation_string(
                speed, contype, False, service_name_final
            ),
        }

        return result

    except json.JSONDecodeError as e:
        logger.error("Ошибка парсинга JSON для логина %s: %s", login, e)
        raise HTTPException(
            status_code=500, detail=f"Некорректные данные в Redis: {e}"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка анализа логина %s: %s", login, e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Ошибка анализа логина: {e}"
        ) from e


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


def _format_time(timestamp: float) -> str:
    """Форматирует timestamp в читаемый вид."""
    try:
        return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(timestamp))
    except (OSError, ValueError):
        return f"timestamp:{timestamp}"


def _get_inactive_reason(
    service, current_time: float, is_inet_service: bool
) -> Optional[str]:
    """Определяет причину неактивности услуги."""
    if not is_inet_service:
        return "Не INET-сервис"

    if not getattr(service, "intervals", None):
        return "Нет интервалов"

    # Проверяем все интервалы
    has_future_intervals = False
    has_past_intervals = False

    for interval in service.intervals:
        if interval.end < current_time:
            has_past_intervals = True
        elif interval.begin > current_time:
            has_future_intervals = True

    if has_past_intervals and not has_future_intervals:
        return "Все интервалы истекли"
    elif has_future_intervals and not has_past_intervals:
        return "Интервалы еще не начались"
    elif has_past_intervals and has_future_intervals:
        return "Между интервалами"

    return "Нет подходящих интервалов"
