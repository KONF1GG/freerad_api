"""Операции авторизации RADIUS."""

import asyncio
import json
import logging
import time
from typing import Any, Dict
from fastapi import HTTPException

from ...config.settings import SESSION_LIMIT
from ...utils.data_prepare import get_username_onu_mac_vlan_from_data

from ...models.schemas import LoginSearchResult, VideoLoginSearchResult
from ..monitoring.service_utils import check_service_expiry

from ..storage.search_operations import (
    find_login_by_session,
    find_sessions_by_login,
)
from ..storage.queue_operations import send_auth_log_to_queue
from ...models import AuthRequest, AuthResponse, AuthDataLog
from ...utils import nasportid_parse, mac_from_hex
from ...core.metrics import track_function
from ...clients.redis_client import execute_redis_command

logger = logging.getLogger(__name__)


async def _get_psiface_description(
    nasportid: Dict[str, Any], redis, nas_ip: str | None
) -> str:
    """Возвращает расшифровку psiface из Redis (JSON.GET psifaces) по региону NAS IP.

    Ожидается структура:
      {
        "region": {"nas_ips": ["10.10.x.x"], "values": {"ps10": "..."}}
      }
    Если NAS IP не указан или регион не найден, возвращает пустую строку.
    """
    try:
        psiface = nasportid.get("psiface") or ""
        if not psiface or not nas_ip:
            return ""

        raw = await execute_redis_command(redis, "JSON.GET", "psifaces")
        if raw is None:
            return ""

        # Приводим к dict вне зависимости от формата
        if isinstance(raw, dict):
            data = raw
        else:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="ignore")
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                    # Некоторые клиенты отдают строку внутри строки -> пробуем второй раз
                    if isinstance(data, str):
                        data = json.loads(data)
                except Exception:
                    logger.warning(
                        "psifaces: не удалось распарсить строку JSON, возвращаю пусто"
                    )
                    return ""
            else:
                logger.warning(
                    "psifaces: неожиданный тип ответа: %s", type(raw).__name__
                )
                return ""

        for region_data in data.values():
            if not isinstance(region_data, dict):
                continue
            if nas_ip in region_data.get("nas_ips", []):
                values = region_data.get("values", {})
                return values.get(psiface, "") or ""

        return ""
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as err:
        logger.warning("Ошибка чтения psifaces из Redis: %s", err)
        return ""


@track_function("radius", "auth")
async def auth(data: AuthRequest, redis) -> Dict[str, Any]:
    """Авторизация пользователя"""

    try:
        logger.info("Попытка авторизации пользователя: %s", data.User_Name)
        (
            username,
            onu_mac,
            vlan,
            is_mac_username,
        ) = await get_username_onu_mac_vlan_from_data(data)

        login: (
            LoginSearchResult | VideoLoginSearchResult | None
        ) = await find_login_by_session(username, onu_mac, vlan, is_mac_username, redis)

        auth_response = AuthResponse()  # type: ignore
        nasportid = nasportid_parse(data.NAS_Port_Id)

        # Вычисляем MAC адрес один раз для переиспользования
        mac_address = mac_from_hex(data.ADSL_Agent_Remote_Id)

        # Временная диагностика: логируем, что возвращает Redis по JSON.GET psifaces
        try:
            _raw_psifaces = await execute_redis_command(redis, "JSON.GET", "psifaces")
            logger.warning(
                "psifaces JSON.GET returned: %r (type=%s)",
                _raw_psifaces,
                type(_raw_psifaces).__name__,
            )
        except Exception as _e:
            logger.error("psifaces JSON.GET failed: %s", _e, exc_info=True)

        # Рассчитать описание psiface для логирования
        psifaces_description = await _get_psiface_description(
            nasportid, redis, data.NAS_IP_Address
        )

        logger.warning(f"psifaces_description: {psifaces_description}")

        # Ситуация с отсутствием опции 82 (ADSL_Agent_Remote_Id) в запросе с OLT CDATA 11xx
        # Приходит пакет с svlan = 5xx, но без опции 82, делаем reject
        if (
            nasportid["svlan"][0] == "5"
            and onu_mac == ""
            and data.Framed_Protocol != "PPP"
        ):
            auth_response.reply_message = {
                "value": "Remote ID (Option82) not found in packet from 5xx svlan (OLT bug)"
            }
            auth_response.control_auth_type = {"value": "Reject"}

            asyncio.create_task(
                _save_auth_log(
                    data,
                    login,
                    "Access-Reject",
                    "Remote ID (Option82) not found in packet from 5xx svlan (OLT bug)",
                    psifaces_description=psifaces_description,
                )
            )
            return auth_response.to_radius_with_additional_attrs(
                login.radiusAttrs if login else None
            )

        # Пользователь не найден
        if not login:
            logger.warning("Пользователь не найден: %s", data.User_Name)

            # Специальная логика для NAS 10.10.15.212 с PPP протоколом
            if data.NAS_IP_Address == "10.10.15.212" and data.Framed_Protocol == "PPP":
                logger.warning(
                    "Reject для NAS %s с PPP протоколом без логина: %s",
                    data.NAS_IP_Address,
                    data.User_Name,
                )
                auth_response.reply_message = {
                    "value": f"User not found [{data.User_Name}], {mac_address}"
                }
                auth_response.control_auth_type = {"value": "Reject"}

                asyncio.create_task(
                    _save_auth_log(
                        data,
                        login,
                        "Access-Reject",
                        f"User not found [{data.User_Name}], {mac_address}",
                        psifaces_description=psifaces_description,
                    )
                )
                return auth_response.to_radius_with_additional_attrs(
                    login.radiusAttrs if login else None
                )

            # Обычная логика для остальных случаев
            auth_response = _build_noinet_novlan(
                auth_response, f"User not found [{data.User_Name}], {mac_address}"
            )
            asyncio.create_task(
                _save_auth_log(
                    data,
                    login,
                    "Access-Accept",
                    f"User not found [{data.User_Name}], {mac_address}",
                    psifaces_description=psifaces_description,
                )
            )
            return auth_response.to_radius_with_additional_attrs(
                login.radiusAttrs if login else None
            )

        # Обработка по типу авторизации
        if login.auth_type == "VIDEO":
            auth_response = await _handle_video_auth(data, login, auth_response)
        else:
            auth_response = await _handle_regular_auth(
                data, login, auth_response, nasportid, redis
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

        asyncio.create_task(
            _save_auth_log(
                data,
                login,
                reply_code,
                reason_text or "Authorization successful",
                psifaces_description=psifaces_description,
            )
        )

        logger.info(
            "Авторизация завершена: %s для пользователя %s", reply_code, data.User_Name
        )
        return auth_response.to_radius_with_additional_attrs(login.radiusAttrs)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка при обработке авторизации: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


def _build_noinet_novlan(auth_response: AuthResponse, reason: str) -> AuthResponse:
    """Строит ответ NOINET-NOVLAN"""
    auth_response.reply_message = {"value": reason}
    auth_response.reply_framed_pool = "novlan"
    # Для отклоненных запросов используем bng (по умолчанию)
    auth_response.reply_erx_virtual_router_name = "bng"
    auth_response.reply_erx_service_activate = "NOINET-NOVLAN()"
    auth_response.control_auth_type = {"value": "Accept"}
    return auth_response


async def _handle_video_auth(
    data: AuthRequest, login: VideoLoginSearchResult, auth_response: AuthResponse
) -> AuthResponse:
    """Обрабатывает авторизацию видеокамер"""
    logger.info("Авторизация видеокамеры: %s", login.key)
    auth_response.reply_framed_ip_address = login.ip_addr
    auth_response.reply_erx_service_activate = "INET-VIDEO()"
    auth_response.reply_erx_virtual_router_name = "video"
    auth_response.reply_nas_port_id = f"{data.User_Name or ''} | {login.host or ''} | {data.ADSL_Agent_Remote_Id or ''}"
    auth_response.reply_message = {"value": f"Session type: {login.auth_type or ''}"}
    auth_response.control_auth_type = {"value": "Accept"}
    return auth_response


async def _handle_regular_auth(
    data: AuthRequest,
    login: LoginSearchResult,
    auth_response: AuthResponse,
    nasportid: Dict[str, Any],
    redis,
) -> AuthResponse:
    """Обрабатывает авторизацию обычных пользователей"""
    sessions = await find_sessions_by_login(login.login or "", redis, login)
    session_count = len(sessions)

    # Проверяем лимит сессий
    if session_count >= SESSION_LIMIT:
        logger.warning(
            "Превышен лимит сессий (%s) для пользователя %s",
            session_count,
            login.login,
        )
        auth_response.reply_message = {
            "value": f"Session limit exceeded: {session_count}, login: {login.login or ''}"
        }
        auth_response.control_auth_type = {"value": "Reject"}

        asyncio.create_task(
            _save_auth_log(
                data,
                login,
                "Access-Reject",
                f"Session limit exceeded [{data.User_Name} {login.login}]",
                psifaces_description=await _get_psiface_description(
                    nasportid, redis, data.NAS_IP_Address
                ),
            )
        )
        raise HTTPException(
            status_code=403,
            detail=f"Session limit exceeded [{data.User_Name} {login.login}]",
        )

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

        asyncio.create_task(
            _save_auth_log(
                data,
                login,
                "Access-Reject",
                f"Static IP limit exceeded [{login.login} {login.ip_addr}]",
                psifaces_description=await _get_psiface_description(
                    nasportid, redis, data.NAS_IP_Address
                ),
            )
        )
        raise HTTPException(
            status_code=403,
            detail=f"Static IP limit exceeded [{login.login} {login.ip_addr}]",
        )

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

        contype = getattr(
            getattr(getattr(login, "servicecats", None), "internet", None),
            "contype",
            None,
        )

        if contype == "social":
            auth_response.reply_erx_service_activate = "INET-SOCIAL()"
        else:
            auth_response.reply_erx_service_activate = f"INET-FREEDOM({calc_speed}k)"

    else:
        auth_response.reply_erx_service_activate = "NOINET-NOMONEY()"

    # Реальник
    if login.ip_addr:
        auth_response.reply_framed_ip_address = login.ip_addr
        auth_response.reply_erx_virtual_router_name = "bng-real"
    # Серые пулы
    else:
        auth_response.reply_erx_virtual_router_name = "bng"
        auth_response.reply_framed_pool = "pool-" + nasportid["psiface"]

    # IPv6 только одна сессия и активная
    if (
        getattr(login, "ipv6", None)
        and not service_should_be_blocked
        and session_count == 0
    ):
        auth_response.reply_framed_ipv6_prefix = login.ipv6
        auth_response.reply_delegated_ipv6_prefix = getattr(login, "ipv6_pd", "")

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
    data: AuthRequest,
    login: LoginSearchResult | VideoLoginSearchResult | None,
    reply_code: str,
    reason: str,
    psifaces_description: str | None = None,
) -> None:
    """Сохраняет лог авторизации"""
    try:
        speed_val = None
        if login and login.login:
            speed_val = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "speed",
                None,
            )

        log_entry = AuthDataLog(
            login=getattr(login, "login", None) if login else None,
            username=data.User_Name,
            password=getattr(login, "password", None) if login else None,
            callingstationid=data.Calling_Station_Id,
            nasipaddress=data.NAS_IP_Address,
            reply=reply_code,
            reason=reason,
            speed=float(speed_val) if speed_val not in (None, "") else None,
            agentremoteid=data.ADSL_Agent_Remote_Id,
            agentcircuitid=data.ADSL_Agent_Circuit_Id,
            nasportid=data.NAS_Port_Id,
            nasport=data.NAS_Port,
            service_type=data.Service_Type,
            acct_session_id=data.Acct_Session_Id,
            framed_protocol=data.Framed_Protocol,
            chap_auth=1 if data.CHAP_Password else 0,
            nas_identifier=data.NAS_Identifier,
            nas_port_type=data.NAS_Port_Type,
            pppoe_description=data.ERX_Pppoe_Description,
            dhcp_first_relay=data.ERX_DHCP_First_Relay_IPv4_Address,
            psifaces_description=(psifaces_description or None),
        )
        await send_auth_log_to_queue(log_entry)
    except (ValueError, TypeError, AttributeError) as log_err:
        logger.error("Не удалось записать лог авторизации: %s", log_err, exc_info=True)
    except Exception as log_err:
        logger.error(
            "Неожиданная ошибка при записи лога авторизации: %s", log_err, exc_info=True
        )
