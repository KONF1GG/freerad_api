import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException

from config import RADIUS_SESSION_PREFIX
from crud import (
    ch_save_session,
    ch_save_traffic,
    enrich_session_with_login,
    find_login_by_session,
    find_sessions_by_login,
    get_session_from_redis,
    process_traffic_data,
    save_auth_log,
    save_session_to_redis,
    delete_session_from_redis,
    update_main_session_service,
)
from schemas import (
    AccountingData,
    AccountingResponse,
    AuthDataLog,
    AuthRequest,
    AuthResponse,
    SessionData,
)
from utils import nasportid_parse

logger = logging.getLogger(__name__)


async def send_coa_session_kill(session_req) -> bool:
    """Отправка команды на убийство сессии через CoA"""
    ...


async def send_coa_session_set(session_req, attributes: Dict[str, Any]) -> bool:
    """Отправка команды на обновление атрибутов сессии через CoA"""
    ...


async def process_accounting(data: AccountingData) -> AccountingResponse:
    """Основная функция обработки аккаунтинга"""
    start_time = time.time()
    status = "success"

    try:
        session_req = data
        packet_type = session_req.Acct_Status_Type
        session_unique_id = session_req.Acct_Unique_Session_Id
        event_time = session_req.Event_Timestamp
        event_timestamp = int(
            session_req.Event_Timestamp.astimezone(timezone.utc).timestamp()
        )

        logger.info(
            f"Обработка аккаунтинга: {packet_type} для сессии {session_unique_id}"
        )
        # Получаем активную сессию из Redis
        redis_key = f"{RADIUS_SESSION_PREFIX}{session_unique_id}"
        session_stored, login = await asyncio.gather(
            get_session_from_redis(redis_key),
            find_login_by_session(session_req),
        )

        # Добавляем данные логина в данные сессии
        session_req = await enrich_session_with_login(session_req, login)

        service = session_req.ERX_Service_Session
        is_service_session = bool(service)

        # Обработка сервисной сессии
        if service and login:
            session_id = session_req.Acct_Session_Id
            if ":" in session_id:
                logger.info(f"Обработка сервисной сессии {session_id}")

                # Обновляем основную сессию с информацией о сервисе
                await update_main_session_service(session_req)

        # Проверка смены логина
        if (
            session_stored
            and login
            and hasattr(login, "login")
            and session_stored.login != login.login
        ):
            logger.info(
                f"Логин изменился с {session_stored.login} "
                f"на {login.login}, убиваем сессию"
            )
            await send_coa_session_kill(session_stored.model_dump(by_alias=True))

        # Соединяем счетчики
        session_req = await process_traffic_data(session_req)

        if session_stored:
            logger.info("Обогащение существующей сессии новыми данными")
            session_stored_dict = session_stored.model_dump(by_alias=True)
            session_req_dict = session_req.model_dump(by_alias=True)
            # Если сессия не сервисная, не обновляем ERX_Service_Session
            if not is_service_session and "ERX-Service-Session" in session_req_dict:
                session_req_dict.pop("ERX-Service-Session")
            session_stored_dict.update(session_req_dict)
            session_new = SessionData(**session_stored_dict)
        else:
            logger.info("Создание новой сессии из входящих данных")
            session_req_dict = session_req.model_dump(by_alias=True)
            if not is_service_session and "ERX-Service-Session" in session_req_dict:
                session_req_dict.pop("ERX-Service-Session")
            session_new = SessionData(**session_req_dict)

        # Обработка по типу пакета
        # Если пакет типа Start, создаем новую сессию
        if packet_type == "Start":
            logger.info("Обработка пакета START")
            session_new.Acct_Start_Time = event_time
            session_new.Acct_Update_Time = event_time
            session_new.Acct_Session_Time = 0
            session_new.Acct_Stop_Time = None

            await asyncio.gather(
                save_session_to_redis(session_new, redis_key),
                ch_save_session(session_new),
            )

        # Если пакет типа Interim-Update
        elif packet_type == "Interim-Update":
            # Если сессия уже существует, обновляем ее

            tasks = []
            if session_stored:
                logger.info("Обработка Interim-Update для существующей сессии")
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, session_stored))
            # Если сессия не существует, создаем новую
            else:
                logger.warning("Interim-Update без активной сессии, создаем новую")
                session_new.Acct_Start_Time = datetime.fromtimestamp(
                    event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
                )
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, None))

            if not is_service_session:
                tasks.append(save_session_to_redis(session_new, redis_key))
            tasks.append(ch_save_session(session_new))
            await asyncio.gather(*tasks)

        # Если пакет типа Stop, завершаем сессию
        elif packet_type == "Stop":
            logger.info("Обработка пакета STOP")
            session_new.Acct_Stop_Time = event_time
            session_new.Acct_Update_Time = event_time

            if not session_stored:
                session_new.Acct_Start_Time = datetime.fromtimestamp(
                    event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
                )
            else:
                session_new.Acct_Start_Time = session_stored.Acct_Start_Time

            await asyncio.gather(
                ch_save_session(session_new, stoptime=True),
                ch_save_traffic(
                    session_new,
                    session_stored if session_stored else None,
                ),
                delete_session_from_redis(redis_key)
                if session_stored
                else asyncio.sleep(0),
            )

        else:
            logger.error(f"Неизвестный тип пакета: {packet_type}")
            return AccountingResponse(
                action="log",
                reason=f"Неизвестный тип пакета: {packet_type}",
                status="error",
            )

        logger.info(f"Успешный аккаунтинг: {packet_type}")
        return AccountingResponse(
            action="noop", reason="processed successfully", session_id=session_unique_id
        )
    except Exception as e:
        status = "error"
        logger.error(f"Ошибка при обработке аккаунтинга: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        exec_time = time.time() - start_time
        logger.debug(f"Аккаунтинг занял {exec_time:.3f}s, статус: {status}")


async def auth(data: AuthRequest) -> Dict[str, Any]:
    """Авторизация пользователя"""

    try:
        logger.info(f"Попытка авторизации: {data}")

        login = await find_login_by_session(data)
        logger.debug(f"Логин: {login}")
        session_limit = 2

        auth_response = AuthResponse()  # type: ignore
        nasportid = nasportid_parse(data.NAS_Port_Id)

        # Договор найден, авторизуем
        if login and login.auth_type != "VIDEO":
            sessions = await find_sessions_by_login(login.login or "")
            session_count = len(sessions)
            logger.debug(f"Сессии найдены: {session_count}")

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

            # Проверяем текущее время для сравнения со сроком действия услуги
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = True

            if timeto is not None:
                try:
                    timeto_float = float(timeto)
                    service_should_be_blocked = timeto_float < now_timestamp
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid timeto value: {timeto}, error: {e}")

            # Выставляем услугу
            if not service_should_be_blocked:
                calc_speed = int(float(speed) * 1100) if speed is not None else 0
                auth_response.reply_erx_service_activate = (
                    f"INET-FREEDOM({calc_speed}k)"
                )

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
                (getattr(login, "ipv6", None))
                and not service_should_be_blocked
                and session_count == 0
            ):
                auth_response.reply_framed_ipv6_prefix = login.ipv6
                auth_response.reply_delegated_ipv6_prefix = getattr(
                    login, "ipv6_pd", ""
                )

            auth_response.reply_erx_virtual_router_name = "bng"

            if login.login == "znvpn7132":
                auth_response.reply_framed_route = "80.244.41.248/29"

            if login.auth_type == "STATIC":
                auth_response.reply_idle_timeout = "10"

            auth_response.reply_nas_port_id = (
                (data.User_Name or "")
                + " | "
                + (login.login or "")
                + " | "
                + (data.ADSL_Agent_Remote_Id or "")
            )

            # PPPoE
            if data.Framed_Protocol == "PPP":
                auth_response.control_cleartext_password = {
                    "value": getattr(login, "password", "")
                }
            # IPOE во всех вариантах
            else:
                auth_response.control_cleartext_password = {"value": "ipoe"}
                auth_response.control_auth_type = {"value": "Accept"}

            auth_response.reply_message = {
                "value": "Session type is " + (login.auth_type or "")
            }

            # Лимит сессий (нужно думать внимательн)
            if session_count >= session_limit:
                auth_response.reply_message = {
                    "value": "Session count over limit:"
                    + str(session_count)
                    + " login:"
                    + (login.login or "")
                }
                auth_response.control_auth_type = {"value": "Reject"}
            # Дублирующая сессия, уже установленная на другом брасе, нужно разрешать
            if data.Framed_IP_Address:
                if getattr(login, "ipv6", None):
                    auth_response.reply_framed_ipv6_prefix = login.ipv6
                    auth_response.reply_delegated_ipv6_prefix = getattr(
                        login, "ipv6_pd", ""
                    )
                auth_response.reply_message = {
                    "value": "Session is duplicated, type " + (login.auth_type or "")
                }
                auth_response.control_auth_type = {"value": "Accept"}
            # Реальник и есть другая сессия
            if session_count > 0 and login.ip_addr:
                auth_response.reply_message = {
                    "value": "Static IP limit:"
                    + str(login.ip_addr)
                    + " login:"
                    + (login.login or "")
                }

                auth_response.control_auth_type = {"value": "Reject"}

        # Видеокамеры
        elif login and login.auth_type == "VIDEO":
            auth_response.reply_framed_ip_address = getattr(login, "ipAddress", "")
            auth_response.reply_erx_service_activate = "INET-VIDEO()"
            auth_response.reply_erx_virtual_router_name = "video"
            auth_response.reply_nas_port_id = (
                (data.User_Name or "")
                + " | "
                + (login.login or "")
                + " | "
                + (data.ADSL_Agent_Remote_Id or "")
            )
            auth_response.reply_message = {
                "value": "Session type is " + (login.auth_type or "")
            }
            auth_response.control_auth_type = {"value": "Accept"}
        # Договор не найден, сессия не авторизована
        else:
            auth_response.reply_message = {
                "value": "Session is unauth, login not found"
            }
            auth_response.reply_framed_pool = "novlan"
            auth_response.reply_erx_virtual_router_name = "bng"
            auth_response.reply_erx_service_activate = "NOVLAN()"
            auth_response.control_auth_type = {"value": "Accept"}

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
            reason=reason_text,
            speed=float(speed_val) if speed_val not in (None, "") else None,
            pool=auth_response.reply_framed_pool,
            agentremoteid=data.ADSL_Agent_Remote_Id,
            agentcircuitid=data.ADSL_Agent_Circuit_Id,
        )
        try:
            await save_auth_log(log_entry)
        except Exception as log_err:
            logger.error(
                f"Не удалось записать лог авторизации: {log_err}", exc_info=True
            )
        return auth_response.to_radius()
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing authorization request: {http_exc}", exc_info=True
        )
        raise
    except Exception as e:
        logger.error(f"Error processing authorization request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


async def check_and_correct_services(login: str):
    sessions = await find_sessions_by_login(login)

    for session in sessions:
        if session.ERX_Service_Session:
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

            # Проверяем текущее время для сравнения со сроком действия услуги
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = False


#             if timeto is not None:
#                 try:
#                     timeto_float = float(timeto)
#                     service_should_be_blocked = timeto_float < now_timestamp
#                 except (ValueError, TypeError) as e:
#                     logger.error(f"Invalid timeto value: {timeto}, error: {e}")

#             # Анализируем состояние с роутера
#             router_says_blocked = "NOINET-NOMONEY" in session.ERX_Service_Session

#             # Сравниваем состояние с роутера с ожидаемым состоянием
#             if router_says_blocked and service_should_be_blocked:
#                 logger.debug(
#                     f"Сервис корректно заблокирован для логина {getattr(login, 'login', 'unknown')}"
#                 )
#             elif router_says_blocked and not service_should_be_blocked:
#                 # Роутер заблокировал, но услуга должна работать - разблокируем
#                 logger.warning(
#                     f"Роутер неправильно заблокировал услугу для логина {getattr(login, 'login', 'unknown')}, разблокировка"
#                 )

#                 # Устанавливаем правильную скорость
#                 if speed:
#                     expected_speed_mb = float(speed) * 1.1
#                     coa_attributes = {
#                         "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
#                     }
#                     await send_coa_session_set(session_req, coa_attributes)

#                     return AccountingResponse(
#                         action="update",
#                         reason="router incorrectly blocked service, unblocked",
#                         coa_attributes=coa_attributes,
#                         session_id=session_unique_id,
#                     )
#             elif not router_says_blocked and service_should_be_blocked:
#                 # Роутер не заблокировал, но услуга должна быть заблокирована - блокируем
#                 logger.warning(
#                     f"Услуга {getattr(login, 'login', 'unknown')} должна быть заблокирована, но роутер этого не сделал"
#                 )
#                 await send_coa_session_kill(session_req)

#                 return AccountingResponse(
#                     action="kill",
#                     reason="service expired",
#                     session_id=session_unique_id,
#                 )
#             else:
#                 # Роутер не заблокировал и услуга не должна быть заблокирована - проверяем скорость
#                 match = re.search(r"\(([\d.]+k)\)", service)
#                 if match:
#                     service_speed_mb = (
#                         float(match.group(1).replace("k", "")) / 1000
#                     )  # k -> Mb
#                     expected_speed_mb = float(speed) * 1.1 if speed else 0
#                     if abs(service_speed_mb - expected_speed_mb) >= 0.01:
#                         logger.warning(
#                             f"Неправильная скорость: Ожидалось {expected_speed_mb} Mb, получено {service_speed_mb} Mb"
#                         )
#                         # Обновляем параметры сессии
#                         coa_attributes = {
#                             "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
#                         }
#                         await send_coa_session_set(session_req, coa_attributes)

#                         return AccountingResponse(
#                             action="update",
#                             reason="speed mismatch corrected",
#                             coa_attributes=coa_attributes,
#                             session_id=session_unique_id,
#                         )
