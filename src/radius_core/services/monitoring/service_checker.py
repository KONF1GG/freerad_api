"""Проверка и корректировка сервисов RADIUS."""

import logging
import re
import time
import asyncio
from typing import Optional, Any
from fastapi import HTTPException

from radius_core.config.settings import RADIUS_LOGIN_PREFIX
from radius_core.services.storage.search_operations import (
    find_sessions_by_login,
    search_redis,
)

from ...models import SessionData, AccountingResponse
from ..coa.coa_operations import send_coa_session_kill, send_coa_session_set
from ..auth.duplicate_session_handler import (
    find_duplicate_sessions_by_username_vlan,
    find_duplicate_sessions_by_onu_mac,
    find_device_sessions_by_device_data,
    kill_duplicate_sessions,
)
from .service_utils import check_service_expiry
from ...utils import nasportid_parse

logger = logging.getLogger(__name__)


async def check_and_correct_service_state(
    session: SessionData, login_data: Any, login_name: str, rabbitmq=None
) -> Optional[AccountingResponse]:
    """Проверить и скорректировать состояние сервиса"""
    if not session.ERX_Service_Session:
        return None

    # Получаем параметры услуги
    timeto = getattr(
        getattr(getattr(login_data, "servicecats", None), "internet", None),
        "timeto",
        None,
    )
    speed = getattr(
        getattr(getattr(login_data, "servicecats", None), "internet", None),
        "speed",
        None,
    )

    # Проверяем срок действия услуги
    now_timestamp = time.time()
    service_should_be_blocked = check_service_expiry(timeto, now_timestamp)

    # Анализируем состояние с роутера
    router_says_blocked = "NOINET-NOMONEY" in session.ERX_Service_Session

    # Сравниваем состояние с роутера с ожидаемым состоянием
    if router_says_blocked and service_should_be_blocked:
        logger.debug("Сервис корректно заблокирован для логина %s", login_name)
        return None

    elif router_says_blocked and not service_should_be_blocked:
        # Роутер заблокировал, но услуга должна работать - разблокируем
        logger.warning(
            "Роутер неправильно заблокировал услугу для логина %s, разблокировка",
            login_name,
        )
        if speed:
            expected_speed_mb = float(speed) * 1.1
            coa_attributes = {"ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)}
            await send_coa_session_set(session, rabbitmq, coa_attributes)
            return AccountingResponse(
                action="update",
                reason="router incorrectly blocked service, unblocked",
                session_id=session.Acct_Unique_Session_Id,
            )

    elif not router_says_blocked and service_should_be_blocked:
        # Роутер не заблокировал, но услуга должна быть заблокирована - блокируем
        logger.warning(
            "Услуга для логина %s должна быть заблокирована, но роутер этого не сделал",
            login_name,
        )
        await send_coa_session_kill(session, rabbitmq)
        return AccountingResponse(
            action="kill",
            reason="service expired",
            session_id=session.Acct_Unique_Session_Id,
        )
    else:
        # Роутер не заблокировал и услуга не должна быть заблокирована - проверяем скорость
        match = re.search(r"\(([\d.]+k)\)", session.ERX_Service_Session)
        if match and speed:
            service_speed_mb = float(match.group(1).replace("k", "")) / 1000  # k -> Mb
            expected_speed_mb = float(speed) * 1.1

            if abs(service_speed_mb - expected_speed_mb) >= 0.01:
                logger.warning(
                    "Неправильная скорость для %s: ожидалось %s Mb, получено %s Mb",
                    login_name,
                    expected_speed_mb,
                    service_speed_mb,
                )
                coa_attributes = {"ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)}
                await send_coa_session_set(session, rabbitmq, coa_attributes)
                return AccountingResponse(
                    action="update",
                    reason="speed mismatch corrected",
                    session_id=session.Acct_Unique_Session_Id,
                )
    return None


async def check_and_correct_services(key: str, redis, rabbitmq=None):
    """Проверяет и корректирует сервисы для логина или устройства"""

    if key.startswith("device:"):
        await _check_device_services(key, redis, rabbitmq)
    elif key.startswith("login:"):
        await _check_login_services(key, redis, rabbitmq)
    else:
        logger.warning("Неизвестный тип ключа для проверки сервисов: %s", key)
        raise HTTPException(
            status_code=400, detail="Unknown key type for service check"
        )


async def _check_device_services(key: str, redis, rabbitmq=None):
    """Проверяет сервисы для устройства"""
    device_id = key.split(":", 1)[1]
    logger.debug("Проверка устройства: %s", device_id)

    # Получаем данные устройства из Redis
    try:
        device_data = await search_redis(
            redis,
            query=f"@login:{{{device_id}}}",
            index="idx:device",
        )

        if not device_data:
            logger.warning("Данные устройства %s не найдены в Redis", device_id)
            return

        device_ip = getattr(device_data, "ipAddress", None)
        device_mac = getattr(device_data, "mac", None)

        logger.debug(
            "Данные устройства %s: IP=%s, MAC=%s", device_id, device_ip, device_mac
        )

        # Найти конфликтующие сессии
        duplicate_sessions = await find_device_sessions_by_device_data(
            device_ip, device_mac, device_id, redis
        )

        if duplicate_sessions:
            await kill_duplicate_sessions(
                duplicate_sessions, f"device {device_id} IP/MAC mismatch", rabbitmq
            )
            logger.info(
                "Завершено %s конфликтующих сессий для устройства %s",
                len(duplicate_sessions),
                device_id,
            )

    except Exception as e:
        logger.error(
            "Ошибка при обработке устройства %s: %s", device_id, e, exc_info=True
        )


async def _check_login_services(key: str, redis, rabbitmq=None):
    """Проверяет сервисы для логина"""
    login_name = key.split(":", 1)[1]
    logger.debug("Проверка сервисов для логина: %s", login_name)

    # Получаем сессии логина и данные логина
    login_key = f"{RADIUS_LOGIN_PREFIX}{login_name}"
    try:
        sessions, login_data = await asyncio.gather(
            find_sessions_by_login(login_name, redis),
            search_redis(redis, query=login_key, key_type="GET", redis_key=login_key),
        )
    except Exception as e:
        logger.error("Ошибка получения данных для %s: %s", login_name, e)
        return

    logger.debug("Найдено %s сессий для логина %s", len(sessions), login_name)

    # Собираем все задачи по поиску дубликатов
    duplicate_search_tasks = []

    for session in sessions:
        unique_id = session.Acct_Unique_Session_Id
        username = session.User_Name
        onu_mac = session.ADSL_Agent_Remote_Id
        vlan = None

        if session.NAS_Port_Id:
            vlan = nasportid_parse(session.NAS_Port_Id).get("cvlan")

        # Поиск дубликатов по username + VLAN
        if username and vlan:
            duplicate_search_tasks.append(
                find_duplicate_sessions_by_username_vlan(
                    username, vlan, unique_id, redis
                )
            )
        else:
            # Создаем корутину, которая возвращает пустой список
            async def empty_list_vlan():
                return []

            duplicate_search_tasks.append(empty_list_vlan())

        # Поиск дубликатов по ONU MAC
        if onu_mac:
            duplicate_search_tasks.append(
                find_duplicate_sessions_by_onu_mac(onu_mac, unique_id, redis)
            )
        else:
            # Создаем корутину, которая возвращает пустой список
            async def empty_list_mac():
                return []

            duplicate_search_tasks.append(empty_list_mac())

    # Выполняем все поиски параллельно
    if duplicate_search_tasks:
        try:
            duplicate_results = await asyncio.gather(
                *duplicate_search_tasks, return_exceptions=True
            )

            # Собираем все найденные дубликаты
            all_duplicates = []
            for result in duplicate_results:
                if not isinstance(result, Exception) and isinstance(result, list):
                    all_duplicates.extend(result)

            # Завершаем все дубликаты одним пакетом
            if all_duplicates:
                await kill_duplicate_sessions(
                    all_duplicates, "login session duplicates", rabbitmq
                )
        except Exception as e:
            logger.error("Ошибка при поиске дубликатов: %s", e, exc_info=True)

    # Проверяем и корректируем состояние сервисов для каждой сессии
    for session in sessions:
        try:
            correction_result = await check_and_correct_service_state(
                session, login_data, login_name, rabbitmq
            )
            if correction_result:
                return correction_result
        except Exception as e:
            logger.error(
                "Ошибка при проверке сервиса для сессии %s: %s",
                session.Acct_Unique_Session_Id,
                e,
                exc_info=True,
            )
