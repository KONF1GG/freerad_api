"""Операции авторизации RADIUS."""

import logging
import time
from typing import Any, Dict
from fastapi import HTTPException

from radius_core.services.monitoring.service_utils import check_service_expiry

from ..storage.search_operations import (
    find_login_by_session,
    find_sessions_by_login,
)
from ..storage.queue_operations import save_auth_log_to_queue
from ...models import AuthRequest, AuthResponse, AuthDataLog
from ...utils import nasportid_parse
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("radius", "auth")
async def auth(data: AuthRequest, redis=None) -> Dict[str, Any]:
    """Авторизация пользователя"""

    try:
        logger.info("Попытка авторизации пользователя: %s", data.User_Name)

        login = await find_login_by_session(data, redis)
        logger.debug("Данные логина: %s", login)
        session_limit = 2

        auth_response = AuthResponse()  # type: ignore
        nasportid = nasportid_parse(data.NAS_Port_Id)

        # Пользователь не найден
        if not login:
            logger.warning("Пользователь не найден: %s", data.User_Name)
            auth_response = _build_reject_response(auth_response, "User not found")
            await _save_auth_log(data, None, "Access-Accept", "User not found")
            return auth_response.to_radius()

        # Обработка по типу авторизации
        if login.auth_type == "VIDEO":
            auth_response = await _handle_video_auth(data, login, auth_response)
        else:
            auth_response = await _handle_regular_auth(
                data, login, auth_response, nasportid, session_limit, redis
            )

        # Определяем код ответа
        auth_type_val = (
            auth_response.control_auth_type.get("value")
            if auth_response.control_auth_type
            else None
        )
        reply_code = "Access-Reject" if auth_type_val == "Reject" else "Access-Accept"

        reason_text = (
            auth_response.reply_message.get("value")
            if auth_response.reply_message
            else None
        )

        # Логируем успешную авторизацию
        if reply_code == "Access-Accept":
            await _save_auth_log(
                data, login, reply_code, reason_text or "Authorization successful"
            )

        logger.info(
            "Авторизация завершена: %s для пользователя %s", reply_code, data.User_Name
        )
        return auth_response.to_radius()

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка при обработке авторизации: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _build_reject_response(auth_response: AuthResponse, reason: str) -> AuthResponse:
    """Строит ответ с отклонением авторизации"""
    auth_response.reply_message = {"value": reason}
    auth_response.reply_framed_pool = "novlan"
    auth_response.reply_erx_virtual_router_name = "bng"
    auth_response.reply_erx_service_activate = "NOVLAN()"
    auth_response.control_auth_type = {"value": "Accept"}
    return auth_response


async def _handle_video_auth(
    data: AuthRequest, login: Any, auth_response: AuthResponse
) -> AuthResponse:
    """Обрабатывает авторизацию видеокамер"""
    logger.debug("Авторизация видеокамеры: %s", login.login)
    auth_response.reply_framed_ip_address = getattr(login, "ipAddress", "")
    auth_response.reply_erx_service_activate = "INET-VIDEO()"
    auth_response.reply_erx_virtual_router_name = "video"
    auth_response.reply_nas_port_id = f"{data.User_Name or ''} | {login.login or ''} | {data.ADSL_Agent_Remote_Id or ''}"
    auth_response.reply_message = {"value": f"Session type: {login.auth_type or ''}"}
    auth_response.control_auth_type = {"value": "Accept"}
    return auth_response


async def _handle_regular_auth(
    data: AuthRequest,
    login: Any,
    auth_response: AuthResponse,
    nasportid: Dict[str, Any],
    session_limit: int,
    redis,
) -> AuthResponse:
    """Обрабатывает авторизацию обычных пользователей"""
    sessions = await find_sessions_by_login(login.login or "", redis)
    session_count = len(sessions)
    logger.debug("Найдено активных сессий: %s", session_count)

    # Проверяем лимит сессий
    if session_count >= session_limit:
        logger.warning(
            "Превышен лимит сессий (%s) для пользователя %s",
            session_count,
            login.login,
        )
        auth_response.reply_message = {
            "value": f"Session limit exceeded: {session_count}, login: {login.login or ''}"
        }
        auth_response.control_auth_type = {"value": "Reject"}

        await _save_auth_log(data, login, "Access-Reject", "Session limit exceeded")
        raise HTTPException(status_code=403, detail="Session limit exceeded")

    # Настраиваем сервисы
    auth_response = _configure_regular_services(
        auth_response, login, nasportid, data, session_count
    )

    # Проверяем дублирующие сессии
    if data.Framed_IP_Address:
        auth_response = _handle_duplicate_session(auth_response, login)

    # Проверяем статический IP
    if session_count > 0 and login.ip_addr:
        logger.warning(
            "Превышен лимит для статического IP %s, пользователь %s",
            login.ip_addr,
            login.login,
        )
        auth_response.reply_message = {
            "value": f"Static IP limit: {login.ip_addr}, login: {login.login or ''}"
        }
        auth_response.control_auth_type = {"value": "Reject"}

        await _save_auth_log(data, login, "Access-Reject", "Static IP limit exceeded")
        raise HTTPException(status_code=403, detail="Static IP limit exceeded")

    return auth_response


def _configure_regular_services(
    auth_response: AuthResponse,
    login: Any,
    nasportid: Dict[str, Any],
    data: AuthRequest,
    session_count: int,
) -> AuthResponse:
    """Настраивает сервисы для обычных пользователей"""
    timeto = getattr(
        getattr(getattr(login, "servicecats", None), "internet", None),
        "timeto",
        None,
    )
    speed = getattr(
        getattr(getattr(login, "servicecats", None), "internet", None),
        "speed",
        None,
    )

    # Проверяем срок действия услуги
    now_timestamp = time.time()
    service_should_be_blocked = check_service_expiry(timeto, now_timestamp)

    # Выставляем услугу
    if not service_should_be_blocked:
        calc_speed = int(float(speed) * 1100) if speed is not None else 0
        auth_response.reply_erx_service_activate = f"INET-FREEDOM({calc_speed}k)"
    else:
        auth_response.reply_erx_service_activate = "NOINET-NOMONEY()"

    # Реальник
    if login.ip_addr:
        auth_response.reply_framed_ip_address = login.ip_addr
    # Серые пулы
    else:
        auth_response.reply_framed_pool = "pool-" + nasportid["psiface"]

    # IPv6 только одна сессия и активная
    if (
        getattr(login, "ipv6", None)
        and not service_should_be_blocked
        and session_count == 0
    ):
        auth_response.reply_framed_ipv6_prefix = login.ipv6
        auth_response.reply_delegated_ipv6_prefix = getattr(login, "ipv6_pd", "")

    auth_response.reply_erx_virtual_router_name = "bng"

    if login.login == "znvpn7132":
        auth_response.reply_framed_route = "80.244.41.248/29"

    if login.auth_type == "STATIC":
        auth_response.reply_idle_timeout = "10"

    auth_response.reply_nas_port_id = f"{data.User_Name or ''} | {login.login or ''} | {data.ADSL_Agent_Remote_Id or ''}"

    # PPPoE
    if data.Framed_Protocol == "PPP":
        auth_response.control_cleartext_password = {
            "value": getattr(login, "password", "")
        }
    else:
        auth_response.control_cleartext_password = {"value": "ipoe"}
        auth_response.control_auth_type = {"value": "Accept"}

    auth_response.reply_message = {"value": f"Session type: {login.auth_type or ''}"}

    return auth_response


def _handle_duplicate_session(auth_response: AuthResponse, login: Any) -> AuthResponse:
    """Обрабатывает дублирующую сессию"""
    if getattr(login, "ipv6", None):
        auth_response.reply_framed_ipv6_prefix = login.ipv6
        auth_response.reply_delegated_ipv6_prefix = getattr(login, "ipv6_pd", "")
    auth_response.reply_message = {
        "value": "Session is duplicated, type " + (login.auth_type or "")
    }
    auth_response.control_auth_type = {"value": "Accept"}
    return auth_response


async def _save_auth_log(
    data: AuthRequest, login: Any, reply_code: str, reason: str
) -> None:
    """Сохраняет лог авторизации"""
    try:
        speed_val = None
        if login:
            speed_val = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "speed",
                None,
            )

        log_entry = AuthDataLog(
            username=getattr(login, "login", None),
            password=getattr(login, "password", None),
            callingstationid=data.Calling_Station_Id,
            nasipaddress=data.NAS_IP_Address,
            reply=reply_code,
            reason=reason,
            speed=float(speed_val) if speed_val not in (None, "") else None,
            pool=getattr(data, "reply_framed_pool", None),
            agentremoteid=data.ADSL_Agent_Remote_Id,
            agentcircuitid=data.ADSL_Agent_Circuit_Id,
        )
        await save_auth_log_to_queue(log_entry)
    except Exception as log_err:
        logger.error("Не удалось записать лог авторизации: %s", log_err, exc_info=True)
