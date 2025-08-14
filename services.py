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
    save_auth_log_to_queue,
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


def _check_service_expiry(timeto: Any, now_timestamp: float) -> bool:
    """Проверяет истек ли срок действия услуги"""
    if timeto is None:
        return True

    try:
        timeto_float = float(timeto)
        return timeto_float < now_timestamp
    except (ValueError, TypeError) as e:
        logger.error(f"Некорректное значение timeto: {timeto}, ошибка: {e}")
        return True


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
        logger.error(f"Не удалось записать лог авторизации: {log_err}", exc_info=True)


async def send_coa_session_kill(session_req) -> bool:
    """
    Отправка команды на завершение сессии через CoA
    """
    # TODO: Реализовать отправку CoA команды для завершения сессии
    logger.debug(f"Отправка CoA команды на завершение сессии: {session_req}")
    return True


async def send_coa_session_set(session_req, attributes: Dict[str, Any]) -> bool:
    """
    Отправка команды на обновление атрибутов сессии через CoA
    """
    # TODO: Реализовать отправку CoA команды для обновления атрибутов
    logger.debug(
        f"Отправка CoA команды на обновление сессии: {session_req}, атрибуты: {attributes}"
    )
    return True


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

        # Получаем активную сессию из Redis и данные логина параллельно
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
                logger.debug(f"Обработка сервисной сессии {session_id}")
                await update_main_session_service(session_req)

        # Проверка смены логина
        if (
            session_stored
            and login
            and hasattr(login, "login")
            and session_stored.login != login.login
        ):
            logger.warning(
                f"Логин изменился с {session_stored.login} "
                f"на {login.login}, завершение сессии"
            )
            await send_coa_session_kill(session_stored.model_dump(by_alias=True))

        # Соединяем счетчики трафика
        session_req = await process_traffic_data(session_req)

        # Создаем или обновляем сессию
        if session_stored:
            logger.debug("Обогащение существующей сессии новыми данными")
            session_stored_dict = session_stored.model_dump(by_alias=True)
            session_req_dict = session_req.model_dump(by_alias=True)
            # Для несервисных сессий не обновляем ERX_Service_Session
            if not is_service_session and "ERX-Service-Session" in session_req_dict:
                session_req_dict.pop("ERX-Service-Session")
            session_stored_dict.update(session_req_dict)
            session_new = SessionData(**session_stored_dict)
        else:
            logger.debug("Создание новой сессии из входящих данных")
            session_req_dict = session_req.model_dump(by_alias=True)
            if not is_service_session and "ERX-Service-Session" in session_req_dict:
                session_req_dict.pop("ERX-Service-Session")
            session_new = SessionData(**session_req_dict)

        # Обработка по типу пакета
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

        elif packet_type == "Interim-Update":
            tasks = []
            if session_stored:
                logger.debug("Обработка Interim-Update для существующей сессии")
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, session_stored))
            else:
                logger.info("Interim-Update без активной сессии, создание новой")
                session_new.Acct_Start_Time = datetime.fromtimestamp(
                    event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
                )
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, None))

            if not is_service_session:
                tasks.append(save_session_to_redis(session_new, redis_key))
            tasks.append(ch_save_session(session_new))
            await asyncio.gather(*tasks)

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
            raise HTTPException(
                status_code=400, detail=f"Unknown packet type: {packet_type}"
            )

        logger.info(f"Аккаунтинг успешно обработан: {packet_type}")
        return AccountingResponse(
            action="noop", reason="processed successfully", session_id=session_unique_id
        )
    except HTTPException:
        raise
    except Exception as e:
        status = "error"
        logger.error(f"Ошибка при обработке аккаунтинга: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        exec_time = time.time() - start_time
        logger.debug(f"Время обработки аккаунтинга: {exec_time:.3f}s, статус: {status}")


async def auth(data: AuthRequest) -> Dict[str, Any]:
    """Авторизация пользователя"""
    try:
        logger.info(f"Попытка авторизации пользователя: {data.User_Name}")

        login = await find_login_by_session(data)
        logger.debug(f"Данные логина: {login}")
        session_limit = 2

        auth_response = AuthResponse()  # type: ignore
        nasportid = nasportid_parse(data.NAS_Port_Id)

        # Пользователь не найден
        if not login:
            logger.warning(f"Пользователь не найден: {data.User_Name}")
            auth_response.reply_message = {"value": "User not found"}
            auth_response.reply_framed_pool = "novlan"
            auth_response.reply_erx_virtual_router_name = "bng"
            auth_response.reply_erx_service_activate = "NOVLAN()"
            auth_response.control_auth_type = {"value": "Reject"}

            # Логируем попытку авторизации
            await _save_auth_log(data, None, "Access-Reject", "User not found")
            raise HTTPException(status_code=404, detail="User not found")

        # Обычные пользователи (не видеокамеры)
        if login.auth_type != "VIDEO":
            sessions = await find_sessions_by_login(login.login or "")
            session_count = len(sessions)
            logger.debug(f"Найдено активных сессий: {session_count}")

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
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = _check_service_expiry(timeto, now_timestamp)

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
                getattr(login, "ipv6", None)
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

            auth_response.reply_nas_port_id = f"{data.User_Name or ''} | {login.login or ''} | {data.ADSL_Agent_Remote_Id or ''}"

            # PPPoE
            if data.Framed_Protocol == "PPP":
                auth_response.control_cleartext_password = {
                    "value": getattr(login, "password", "")
                }
            else:
                auth_response.control_cleartext_password = {"value": "ipoe"}
                auth_response.control_auth_type = {"value": "Accept"}

            auth_response.reply_message = {
                "value": f"Session type: {login.auth_type or ''}"
            }

            # Лимит сессий (нужно думать внимательн)
            if session_count >= session_limit:
                logger.warning(
                    f"Превышен лимит сессий ({session_count}) для пользователя {login.login}"
                )
                auth_response.reply_message = {
                    "value": f"Session limit exceeded: {session_count}, login: {login.login or ''}"
                }
                auth_response.control_auth_type = {"value": "Reject"}

                await _save_auth_log(
                    data, login, "Access-Reject", "Session limit exceeded"
                )
                raise HTTPException(status_code=403, detail="Session limit exceeded")

            # Дублирующая сессия
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
                logger.warning(
                    f"Превышен лимит для статического IP {login.ip_addr}, пользователь {login.login}"
                )
                auth_response.reply_message = {
                    "value": f"Static IP limit: {login.ip_addr}, login: {login.login or ''}"
                }
                auth_response.control_auth_type = {"value": "Reject"}

                await _save_auth_log(
                    data, login, "Access-Reject", "Static IP limit exceeded"
                )
                raise HTTPException(status_code=403, detail="Static IP limit exceeded")

        # Видеокамеры
        elif login.auth_type == "VIDEO":
            logger.debug(f"Авторизация видеокамеры: {login.login}")
            auth_response.reply_framed_ip_address = getattr(login, "ipAddress", "")
            auth_response.reply_erx_service_activate = "INET-VIDEO()"
            auth_response.reply_erx_virtual_router_name = "video"
            auth_response.reply_nas_port_id = f"{data.User_Name or ''} | {login.login or ''} | {data.ADSL_Agent_Remote_Id or ''}"
            auth_response.reply_message = {
                "value": f"Session type: {login.auth_type or ''}"
            }
            auth_response.control_auth_type = {"value": "Accept"}

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
            f"Авторизация завершена: {reply_code} для пользователя {data.User_Name}"
        )
        return auth_response.to_radius()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при обработке авторизации: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


async def check_and_correct_services(key: str):
    """Проверяет и корректирует сервисы для логина или устройства"""
    if key.startswith("device:"):
        logger.debug(f"Проверка устройства: {key}")
        # TODO: Реализовать логику для устройств
        return

    elif key.startswith("login:"):
        login_name = key.split(":", 1)[1]
        logger.debug(f"Проверка сервисов для логина: {login_name}")

        sessions = await find_sessions_by_login(login_name)

        for session in sessions:
            if not session.ERX_Service_Session:
                continue

            # Получаем параметры услуги
            timeto = getattr(
                getattr(getattr(session, "servicecats", None), "internet", None),
                "timeto",
                None,
            )
            speed = getattr(
                getattr(getattr(session, "servicecats", None), "internet", None),
                "speed",
                None,
            )

            # Проверяем срок действия услуги
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = _check_service_expiry(timeto, now_timestamp)

            # Анализируем состояние с роутера
            router_says_blocked = "NOINET-NOMONEY" in session.ERX_Service_Session

            # Сравниваем состояние с роутера с ожидаемым состоянием
            if router_says_blocked and service_should_be_blocked:
                logger.debug(f"Сервис корректно заблокирован для логина {login_name}")

            elif router_says_blocked and not service_should_be_blocked:
                # Роутер заблокировал, но услуга должна работать - разблокируем
                logger.warning(
                    f"Роутер неправильно заблокировал услугу для логина {login_name}, разблокировка"
                )

                if speed:
                    expected_speed_mb = float(speed) * 1.1
                    coa_attributes = {
                        "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
                    }
                    await send_coa_session_set(session, coa_attributes)

                    return AccountingResponse(
                        action="update",
                        reason="router incorrectly blocked service, unblocked",
                        session_id=session.Acct_Unique_Session_Id,
                    )

            elif not router_says_blocked and service_should_be_blocked:
                # Роутер не заблокировал, но услуга должна быть заблокирована - блокируем
                logger.warning(
                    f"Услуга для логина {login_name} должна быть заблокирована, но роутер этого не сделал"
                )
                await send_coa_session_kill(session)

                return AccountingResponse(
                    action="kill",
                    reason="service expired",
                    session_id=session.Acct_Unique_Session_Id,
                )
            else:
                # Роутер не заблокировал и услуга не должна быть заблокирована - проверяем скорость
                match = re.search(r"\(([\d.]+k)\)", session.ERX_Service_Session)
                if match and speed:
                    service_speed_mb = (
                        float(match.group(1).replace("k", "")) / 1000
                    )  # k -> Mb
                    expected_speed_mb = float(speed) * 1.1

                    if abs(service_speed_mb - expected_speed_mb) >= 0.01:
                        logger.warning(
                            f"Неправильная скорость для {login_name}: "
                            f"ожидалось {expected_speed_mb} Mb, получено {service_speed_mb} Mb"
                        )
                        # Обновляем параметры сессии
                        coa_attributes = {
                            "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
                        }
                        await send_coa_session_set(session, coa_attributes)

                        return AccountingResponse(
                            action="update",
                            reason="speed mismatch corrected",
                            session_id=session.Acct_Unique_Session_Id,
                        )
    else:
        logger.warning(f"Неизвестный тип ключа для проверки сервисов: {key}")
