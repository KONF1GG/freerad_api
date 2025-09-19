"""Проверка и корректировка сервисов RADIUS."""

import logging
import re
import time
import asyncio
from typing import Optional, Dict, Any
from fastapi import HTTPException

from radius_core.utils.helpers import parse_service_name

from ...config import RADIUS_LOGIN_PREFIX
from ..storage.search_operations import (
    find_sessions_by_login,
    search_redis,
)

from ...models import SessionData, LoginSearchResult
from ...models.schemas import CorrectRequest, ServiceCheckResponse
from ..coa.coa_operations import send_coa_session_kill, send_coa_session_set
from .service_utils import check_service_expiry

logger = logging.getLogger(__name__)


def _get_service_params(
    login_data: LoginSearchResult,
) -> tuple[Optional[int], Optional[str]]:
    """Извлекает параметры услуги из данных логина."""
    servicecats = getattr(login_data, "servicecats", None)
    if not servicecats:
        return None, None

    internet = getattr(servicecats, "internet", None)
    if not internet:
        return None, None

    timeto = getattr(internet, "timeto", None)
    speed = getattr(internet, "speed", None)

    # Если speed в login_data не пустой, используем его
    if login_data.speed and login_data.speed != "0":
        speed = login_data.speed

    return timeto, speed


def _parse_service_speed(service_session: str) -> Optional[float]:
    """Парсит скорость из ERX_Service_Session."""
    try:
        match = re.search(r"\(([\d.]+[km]?)\)", service_session)
        if not match:
            return None

        speed_str = match.group(1)

        if speed_str.endswith("k"):
            return float(speed_str[:-1]) / 1000  # k -> Mb
        elif speed_str.endswith("m"):
            return float(speed_str[:-1])  # m -> Mb
        else:
            # Нет суффикса - это биты в секунду, преобразуем в Mb
            return float(speed_str) / 1000000  # биты -> Mb
    except (ValueError, AttributeError) as e:
        logger.warning("Ошибка парсинга скорости из %s: %s", service_session, e)
        return None





async def check_and_correct_service_state(
    session: SessionData, login_data: LoginSearchResult, login_name: str, channel=None
) -> Optional[Dict[str, Any]]:
    """Проверить и скорректировать состояние сервиса"""
    logger.info(
        "Проверка и корректировка состояния сервиса для сессии %s (%s)",
        session.Acct_Unique_Session_Id,
        session.Acct_Status_Type,
    )

    if not session.ERX_Service_Session:
        return None

    # Получаем параметры услуги
    timeto, speed = _get_service_params(login_data)

    # Проверяем срок действия услуги
    now_timestamp = time.time()
    service_should_be_blocked = check_service_expiry(timeto, now_timestamp)

    # Анализируем состояние с роутера
    router_says_blocked = "NOINET-NOMONEY" in session.ERX_Service_Session

    # Сравниваем состояние с роутера с ожидаемым состоянием
    if router_says_blocked and service_should_be_blocked:
        logger.info("Сервис корректно заблокирован для логина %s", login_name)
        return None

    elif router_says_blocked and not service_should_be_blocked:
        # Роутер заблокировал, но услуга должна работать - разблокируем
        logger.warning(
            "Роутер неправильно заблокировал услугу для логина %s, разблокировка",
            login_name,
        )
        if speed:
            try:
                expected_speed_kb = int(float(speed) * 1100)
                # Получаем текущее название услуги для деактивации
                current_service_name = parse_service_name(session.ERX_Service_Session)
                coa_attributes = {
                    "ERX-Service-Activate:1": "INET-FREEDOM("
                    + str(expected_speed_kb)
                    + "k)",
                    "ERX-Service-Deactivate": current_service_name or "NOINET-NOMONEY",
                }
                reason = f"Router incorrectly blocked service for {login_name}, unblocking with speed {expected_speed_kb}k"
                logger.info(
                    "CoA SET: разблокировка сервиса для сессии %s (%s), атрибуты: %s",
                    session.Acct_Unique_Session_Id,
                    login_name,
                    coa_attributes,
                )
                await send_coa_session_set(
                    session, channel, coa_attributes, reason=reason
                )
                return {
                    "action": "update",
                    "reason": "router incorrectly blocked service, unblocked",
                    "session_id": session.Acct_Unique_Session_Id,
                }
            except (ValueError, TypeError) as e:
                logger.error("Ошибка преобразования скорости %s: %s", speed, e)
                return None

    elif not router_says_blocked and service_should_be_blocked:
        # Роутер не заблокировал, но услуга должна быть заблокирована - блокируем
        logger.warning(
            "Услуга для логина %s должна быть заблокирована, но роутер этого не сделал",
            login_name,
        )
        # Получаем текущее название услуги для деактивации
        current_service_name = parse_service_name(session.ERX_Service_Session)
        reason = f"Service expired for {login_name}, blocking access"
        coa_attributes = {
            "ERX-Service-Activate:1": "NOINET-NOMONEY()",
            "ERX-Service-Deactivate": current_service_name or "INET-FREEDOM",
        }
        logger.info(
            "CoA SET: блокировка сервиса для сессии %s (%s), атрибуты: %s",
            session.Acct_Unique_Session_Id,
            login_name,
            coa_attributes,
        )
        await send_coa_session_set(session, channel, coa_attributes, reason=reason)
        return {
            "action": "kill",
            "reason": "service expired",
            "session_id": session.Acct_Unique_Session_Id,
        }
    else:
        # Роутер не заблокировал и услуга не должна быть заблокирована - проверяем скорость
        if speed:
            service_speed_mb = _parse_service_speed(session.ERX_Service_Session)
            if service_speed_mb is not None:
                try:
                    expected_speed_kb = int(float(speed) * 1100)

                    if abs(service_speed_mb - expected_speed_kb / 1000) >= 0.01:
                        logger.warning(
                            "Неправильная скорость для %s: ожидалось %s k, получено %s Mb",
                            login_name,
                            expected_speed_kb,
                            service_speed_mb,
                        )
                        # Получаем текущее название услуги для деактивации
                        current_service_name = parse_service_name(
                            session.ERX_Service_Session
                        )
                        coa_attributes = {
                            "ERX-Service-Activate:1": "INET-FREEDOM("
                            + str(expected_speed_kb)
                            + "k)",
                            "ERX-Service-Deactivate": current_service_name
                            or "INET-FREEDOM",
                        }
                        reason = f"Speed mismatch for {login_name}: expected {expected_speed_kb}k, got {service_speed_mb}Mb"
                        logger.info(
                            "CoA SET: обновление скорости для сессии %s (%s), атрибуты: %s",
                            session.Acct_Unique_Session_Id,
                            login_name,
                            coa_attributes,
                        )
                        await send_coa_session_set(
                            session, channel, coa_attributes, reason=reason
                        )
                        return {
                            "action": "update",
                            "reason": "speed mismatch corrected",
                            "session_id": session.Acct_Unique_Session_Id,
                        }
                except (ValueError, TypeError) as e:
                    logger.error("Ошибка преобразования скорости %s: %s", speed, e)
                    return None
    return None


async def check_and_correct_services(
    data: CorrectRequest, redis, channel=None
) -> ServiceCheckResponse:
    """Проверяет и корректирует сервисы для логина или устройства"""
    key = data.key
    fields_changed = data.fields_changed
    # Валидация входных данных
    if not key:
        logger.warning("Key is empty")
        raise HTTPException(status_code=400, detail="Key cannot be empty")

    if not redis:
        logger.warning("Redis client is None")
        raise HTTPException(status_code=500, detail="Redis client not available")

    if key.startswith("login:"):
        result = await _check_login_services(key, fields_changed, redis, channel)
        return result or ServiceCheckResponse(
            action="noop", reason="No corrections needed", status="success"
        )

    elif key.startswith("device:"):
        # Возможно будет логика для устройств
        ...
        # await _check_device_services(key, redis, rabbitmq)
        return ServiceCheckResponse(
            action="noop", reason="Device check not implemented", status="success"
        )
    else:
        logger.warning("Неизвестный тип ключа для проверки сервисов: %s", key)
        raise HTTPException(
            status_code=400, detail="Unknown key type for service check"
        )


async def _check_login_services(key: str, fields_changed: bool, redis, channel=None):
    """Проверяет по логину данные в сессиях"""
    login_name = key.split(":", 1)[1]
    logger.info(
        "Проверка по логину данные в сессиях: %s, fields_changed: %s",
        login_name,
        fields_changed,
    )

    # Сначала получаем данные логина
    login_key = f"{RADIUS_LOGIN_PREFIX}{login_name}"
    try:
        login_data: Optional[LoginSearchResult] = await search_redis(
            redis, query=login_key, key_type="GET", redis_key=login_key
        )
        if not login_data:
            logger.warning("Данные логина %s не найдены в Redis", login_name)
            return ServiceCheckResponse(
                action="error",
                reason=f"Login data not found for {login_name}",
                status="error",
            )

        # Теперь ищем сессии, используя данные логина
        logger.info(
            "Ищем сессии для логина %s, данные логина: %s",
            login_name,
            login_data.model_dump() if hasattr(login_data, "json") else str(login_data),
        )
        sessions = await find_sessions_by_login(login_name, redis, login_data)
        logger.info("Найдено %s сессий для логина %s", len(sessions), login_name)
    except Exception as e:
        logger.error("Ошибка получения данных для %s: %s", login_name, e)
        return ServiceCheckResponse(
            action="error",
            reason=f"Error getting data for {login_name}: {str(e)}",
            status="error",
        )

    if not sessions:
        logger.info("Нет сессий для проверки логина %s", login_name)
        return ServiceCheckResponse(
            action="noop",
            reason=f"No sessions found for {login_name}",
            status="success",
        )

    # Логируем id сессии и её тип через запятую
    session_info = [
        f"{getattr(s, 'Acct_Unique_Session_Id', 'NO_ID')}:{getattr(s, 'auth_type', 'UNKNOWN')}"
        for s in sessions
    ]
    logger.info("Сессии для логина %s: %s", login_name, ", ".join(session_info))

    # Если fields_changed=True - убиваем все сессии (включая VIDEO)
    if fields_changed:
        logger.info(
            "Поля логина изменились, убиваем все %s сессий для логина %s",
            len(sessions),
            login_name,
        )

        detailed_reason = f"Login fields changed for {login_name}"

        # Отправляем CoA kill для всех сессий (включая VIDEO)
        kill_tasks = []
        for session in sessions:
            session_auth_type = session.auth_type or "UNKNOWN"
            logger.info(
                "CoA KILL: убийство сессии %s (%s, %s), причина: %s",
                session.Acct_Unique_Session_Id,
                login_name,
                session_auth_type,
                detailed_reason,
            )
            kill_tasks.append(
                send_coa_session_kill(
                    session,
                    channel,
                    reason=detailed_reason,
                )
            )

        # Выполняем все задачи и проверяем результаты
        results = await asyncio.gather(*kill_tasks, return_exceptions=True)

        # Подсчитываем успешные и неудачные операции
        successful = sum(1 for result in results if not isinstance(result, Exception))
        failed = len(results) - successful

        if failed > 0:
            logger.warning(
                "Не удалось отправить CoA kill для %s из %s сессий",
                failed,
                len(sessions),
            )
            # Логируем ошибки
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "Ошибка CoA kill для сессии %s: %s",
                        sessions[i].Acct_Unique_Session_Id,
                        result,
                    )
        else:
            logger.info(
                "CoA kill команды успешно отправлены для всех %s сессий", len(sessions)
            )

        return ServiceCheckResponse(
            action="kill",
            reason=f"Killed {len(sessions)} sessions due to fields change: {detailed_reason}",
            status="success",
        )

    # Если fields_changed=False - проверяем только логин
    login_mismatch_found = False
    for session in sessions:
        session_login = session.login
        if session_login and session_login != login_name:
            logger.warning(
                "Несоответствие логина в сессии %s: ожидался %s, получен %s",
                session.Acct_Unique_Session_Id,
                login_name,
                session_login,
            )
            login_mismatch_found = True

    # Если логин не изменился - проверяем сервисы (исключая VIDEO сессии)
    if not login_mismatch_found:
        # Фильтруем сессии, исключая VIDEO
        non_video_sessions = [s for s in sessions if s.auth_type != "VIDEO"]
        video_sessions = [s for s in sessions if s.auth_type == "VIDEO"]

        logger.info(
            "Логин не изменился, проверяем сервисы для %s сессий логина %s (исключено %s VIDEO сессий)",
            len(non_video_sessions),
            login_name,
            len(video_sessions),
        )

        if video_sessions:
            video_session_ids = [s.Acct_Unique_Session_Id for s in video_sessions]
            logger.info(
                "Пропускаем проверку сервисов для VIDEO сессий: %s",
                ", ".join(video_session_ids),
            )

        # Проверяем и корректируем состояние сервисов только для не-Video сессий
        for session in non_video_sessions:
            try:
                correction_result = await check_and_correct_service_state(
                    session, login_data, login_name, channel
                )
                if correction_result:
                    return ServiceCheckResponse(**correction_result)
            except Exception as e:
                logger.error(
                    "Ошибка при проверке сервиса для сессии %s: %s",
                    session.Acct_Unique_Session_Id,
                    e,
                    exc_info=True,
                )

        # Если дошли до сюда, значит все сервисы корректны
        return ServiceCheckResponse(
            action="noop",
            reason=f"All services checked for {login_name}, no corrections needed",
            status="success",
        )
    else:
        logger.info(
            "Логин изменился, но fields_changed=False - это неожиданная ситуация"
        )
        return ServiceCheckResponse(
            action="error",
            reason="Login mismatch found but fields_changed=False",
            status="error",
        )


# async def _check_device_services(key: str, redis, channel=None):
#     """Проверяет сервисы для устройства"""
#     device_id = key.split(":", 1)[1]
#     # logger.debug("Проверка устройства: %s", device_id)

#     # Получаем данные устройства из Redis
#     try:
#         device_data = await search_redis(
#             redis,
#             query=f"@login:{{{device_id}}}",
#             index="idx:device",
#         )

#         if not device_data:
#             logger.warning("Данные устройства %s не найдены в Redis", device_id)
#             return

#         device_ip = getattr(device_data, "ipAddress", None)
#         device_mac = getattr(device_data, "mac", None)

#         # Найти конфликтующие сессии
#         duplicate_sessions = await find_device_sessions_by_device_data(
#             device_ip, device_mac, device_id, redis
#         )

#         if duplicate_sessions:
#             await kill_duplicate_sessions(
#                 duplicate_sessions, f"device {device_id} IP/MAC mismatch", channel
#             )
#             logger.info(
#                 "Завершено %s конфликтующих сессий для устройства %s",
#                 len(duplicate_sessions),
#                 device_id,
#             )

#     except Exception as e:
#         logger.error(
#             "Ошибка при обработке устройства %s: %s", device_id, e, exc_info=True
#         )
