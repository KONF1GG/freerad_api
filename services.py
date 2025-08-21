import asyncio
import logging
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# from annotated_types import T  # unused
from fastapi import HTTPException
import aio_pika

from config import AMQP_COA_QUEUE, RADIUS_LOGIN_PREFIX, RADIUS_SESSION_PREFIX
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
    search_redis,
    execute_redis_command,
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
from utils import nasportid_parse, is_mac_username, mac_from_username

logger = logging.getLogger(__name__)


async def send_coa_to_queue(request_type: str, session_data: Dict[str, Any], rabbitmq, attributes: Dict[str, Any] = None) -> bool:
    """
    Отправка CoA запроса в очередь RabbitMQ
    
    Args:
        request_type: Тип запроса ("kill" или "update")
        session_data: Данные сессии
        attributes: Атрибуты для изменения (только для update)

    """
    try:
        
        # Объявляем очередь для CoA запросов
        queue = await rabbitmq.declare_queue(
            AMQP_COA_QUEUE,
            durable=True
        )
        
        # Формируем сообщение
        message_data = {
            "type": request_type,
            "session_data": session_data,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "request_id": f"{request_type}_{session_data.get('Acct_Session_Id', 'unknown')}_{int(time.time())}"
        }
        
        # Добавляем атрибуты для update запросов
        if request_type == "update" and attributes:
            message_data["attributes"] = attributes
        
        # Отправляем сообщение в очередь
        await rabbitmq.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message_data, ensure_ascii=False).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=queue.name
        )
        
        logger.debug(f"CoA запрос {request_type} отправлен в очередь для сессии {session_data.get('Acct_Session_Id', 'unknown')}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки CoA запроса в очередь: {e}", exc_info=True)
        return False


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


async def send_coa_session_kill(session_req, rabbitmq) -> bool:
    """
    Отправка команды на завершение сессии через CoA (в очередь)
    """
    try:
        session_data = session_req.model_dump(by_alias=True)

        # Отправляем CoA kill запрос в очередь
        success = await send_coa_to_queue("kill", session_data, rabbitmq)
        
        if success:
            logger.info(f"CoA команда на завершение сессии отправлена в очередь: {session_data.get('Acct_Session_Id', 'unknown')}")
        else:
            logger.warning(f"CoA команда на завершение сессии не была отправлена в очередь: {session_data.get('Acct_Session_Id', 'unknown')}")
        
        return success
        
    except Exception as e:
        logger.error(f"Ошибка отправки CoA команды на завершение сессии в очередь: {e}", exc_info=True)
        return False


async def send_coa_session_set(session_req, rabbitmq, attributes: Dict[str, Any]) -> bool:
    """
    Отправка команды на обновление атрибутов сессии через CoA (в очередь)
    """
    try:
        session_data = session_req.model_dump(by_alias=True)

        # Отправляем CoA update запрос в очередь
        success = await send_coa_to_queue("update", session_data, rabbitmq, attributes)
        
        if success:
            logger.info(
                f"CoA команда на обновление сессии отправлена в очередь: "
                f"{session_data.get('Acct_Session_Id', 'unknown')}, "
                f"атрибуты: {attributes}"
            )
        else:
            logger.warning(
                f"CoA команда на обновление сессии не была отправлена в очередь: "
                f"{session_data.get('Acct_Session_Id', 'unknown')}, "
                f"атрибуты: {attributes}"
            )
        
        return success
        
    except Exception as e:
        logger.error(
            f"Ошибка отправки CoA команды на обновление сессии в очередь: {e}", 
            exc_info=True
        )
        return False


async def _merge_and_close_session(
    session_stored: Any,
    session_req: Any,
    redis_key: str,
    redis,
    event_time,
    session_unique_id: str,
    rabbitmq=None,
    send_coa: bool = False,
    log_msg: Optional[str] = None,
    reason: str = "session closed",
) -> AccountingResponse:
    """Слить данные входящего пакета в существующиую сессию,
    опционально отправить CoA kill и удалить сессию из Redis"""
    # Merge models
    session_stored_dict = session_stored.model_dump(by_alias=True)
    session_req_dict = session_req.model_dump(by_alias=True)
    session_stored_dict.update(session_req_dict)
    session_to_close = SessionData(**session_stored_dict)

    # Update timestamps
    session_to_close.Acct_Stop_Time = event_time
    session_to_close.Acct_Update_Time = event_time

    tasks = [
        ch_save_session(session_to_close, stoptime=True),
        ch_save_traffic(session_to_close, session_stored),
    ]

    if send_coa:
        tasks.append(send_coa_session_kill(session_stored.model_dump(by_alias=True), rabbitmq))

    tasks.append(delete_session_from_redis(redis_key, redis))

    await asyncio.gather(*tasks)

    if log_msg:
        logger.info(log_msg)
    else:
        logger.info(f"Сессия {session_unique_id} сохранена и завершена ({reason})")

    return AccountingResponse(
        action="kill", reason=reason, session_id=session_unique_id
    )


async def process_accounting(
    data: AccountingData, redis=None, rabbitmq=None
) -> AccountingResponse:
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
            get_session_from_redis(redis_key, redis),
            find_login_by_session(session_req, redis),
        )

        # Добавляем данные логина в данные сессии
        session_req = await enrich_session_with_login(session_req, login)

        # Соединяем счетчики трафика
        session_req = await process_traffic_data(session_req)

        service = session_req.ERX_Service_Session
        is_service_session = bool(service)

        # Обработка сервисной сессии
        if service and login:
            session_id = session_req.Acct_Session_Id
            if ":" in session_id:
                logger.debug(f"Обработка сервисной сессии {session_id}")
                await update_main_session_service(session_req, redis)

        # Обработка завершения сессии при изменении логина или его отсутствии
        if session_stored:
            stored_login = getattr(session_stored, "login", None)
            stored_auth_type = getattr(session_stored, "auth_type", None)
            current_login = getattr(login, "login", None)
            current_auth_type = getattr(login, "auth_type", None)

            # 1. Нет логина, но сессия авторизована
            if not login and stored_auth_type != "UNAUTH":
                logger.warning(
                    f"Сессия {session_unique_id} найдена авторизованная, но логин не найден."
                )
                return await _merge_and_close_session(
                    session_stored,
                    session_req,
                    redis_key,
                    redis,
                    event_time,
                    session_unique_id,
                    rabbitmq=rabbitmq,
                    send_coa=True,
                    log_msg=f"Сессия {session_unique_id} завершена: login not found",
                    reason="login not found, session closed",
                )

            # 2. Логин изменился
            if login and  stored_login and stored_login != current_login:
                logger.warning(
                    f"Логин изменился с {stored_login} на {current_login}, завершаем сессию."
                )
                return await _merge_and_close_session(
                    session_stored,
                    session_req,
                    redis_key,
                    redis,
                    event_time,
                    session_unique_id,
                    rabbitmq=rabbitmq,
                    send_coa=True,
                    log_msg=f"Сессия {session_unique_id} завершена: login mismatch",
                    reason="login mismatch, session closed",
                )

            # 3. Сессия была UNAUTH, теперь авторизована
            if stored_auth_type == "UNAUTH" and current_auth_type != "UNAUTH":
                logger.info(
                    f"Сессия {session_unique_id} была UNAUTH, теперь авторизована."
                )
                return await _merge_and_close_session(
                    session_stored,
                    session_req,
                    redis_key,
                    redis,
                    event_time,
                    session_unique_id,
                    rabbitmq=rabbitmq,
                    send_coa=True,
                    log_msg=f"Сессия {session_unique_id} завершена: UNAUTH -> AUTH",
                    reason="session UNAUTH closed (now authorized)",
                )

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
                save_session_to_redis(session_new, redis_key, redis),
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
                tasks.append(save_session_to_redis(session_new, redis_key, redis))
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
                delete_session_from_redis(redis_key, redis)
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


async def auth(data: AuthRequest, redis=None) -> Dict[str, Any]:
    """Авторизация пользователя"""
    try:
        logger.info(f"Попытка авторизации пользователя: {data.User_Name}")

        login = await find_login_by_session(data, redis)
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
            sessions = await find_sessions_by_login(login.login or "", redis)
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


async def _find_duplicate_sessions_by_username_vlan(
    username: str, vlan: str, current_session_id: str, redis
) -> list[SessionData]:
    """Найти дублирующие сессии по username + VLAN"""
    duplicates = []
    try:
        if is_mac_username(username):
            # Поиск по MAC + VLAN для MAC-адресов
            mac = mac_from_username(username).replace(":", r"\:")
            query = f"@mac:{{{mac}}}@vlan:{{{vlan}}}"
        else:
            # Поиск по username + VLAN для обычных логинов
            escaped_username = username.replace("-", r"\-")
            query = f"@username:{{{escaped_username}}}@vlan:{{{vlan}}}"

        result = await execute_redis_command(
            redis, "FT.SEARCH", "idx:radius:session", query
        )

        if result and result[0] > 0:
            num_results = result[0]
            for i in range(num_results):
                fields = result[2 + i * 2]
                if isinstance(fields, list) and len(fields) >= 2 and fields[0] == "$":
                    doc = fields[1]
                    if isinstance(doc, bytes):
                        doc = doc.decode("utf-8")
                    try:
                        session_dict = json.loads(doc)
                        found_session = SessionData(**session_dict)
                        found_uid = getattr(
                            found_session, "Acct_Unique_Session_Id", None
                        )

                        # Исключаем текущую сессию
                        if (
                            found_uid
                            and str(found_uid).strip()
                            != str(current_session_id).strip()
                        ):
                            duplicates.append(found_session)
                    except Exception as e:
                        logger.warning(f"Не удалось разобрать документ сессии: {e}")
    except Exception as e:
        logger.error(f"Ошибка при поиске дублей по username+VLAN: {e}", exc_info=True)

    return duplicates


async def _find_duplicate_sessions_by_onu_mac(
    onu_mac: str, current_session_id: str, redis
) -> list[SessionData]:
    """Найти дублирующие сессии по ONU MAC"""
    duplicates = []
    try:
        query = f"@onu_mac:{{{onu_mac}}}"
        result = await execute_redis_command(
            redis, "FT.SEARCH", "idx:radius:session", query
        )

        if result and result[0] > 0:
            num_results = result[0]
            for i in range(num_results):
                fields = result[2 + i * 2]
                if isinstance(fields, list) and len(fields) >= 2 and fields[0] == "$":
                    doc_data = fields[1]
                    if isinstance(doc_data, bytes):
                        doc_data = doc_data.decode("utf-8")
                    try:
                        session_dict = json.loads(doc_data)
                        found_session = SessionData(**session_dict)
                        found_uid = getattr(
                            found_session, "Acct_Unique_Session_Id", None
                        )

                        # Исключаем текущую сессию
                        if (
                            found_uid
                            and str(found_uid).strip()
                            != str(current_session_id).strip()
                        ):
                            duplicates.append(found_session)
                    except Exception as e:
                        logger.warning(f"Не удалось разобрать документ сессии: {e}")
    except Exception as e:
        logger.error(f"Ошибка при поиске дублей по onu_mac: {e}", exc_info=True)

    return duplicates


async def _find_device_sessions_by_device_data(
    device_ip: Optional[str], device_mac: Optional[str], device_id: str, redis
) -> list[SessionData]:
    """Найти сессии устройства, где IP или MAC не совпадают с ожидаемыми"""
    duplicates = []
    try:
        # Поиск всех активных сессий устройства по device_id или IP
        queries = []

        # Поиск по IP устройства
        if device_ip:
            escaped_ip = device_ip.replace(".", r"\.")
            queries.append(f"@Framed_IP_Address:{{{escaped_ip}}}")

        # Поиск по ID устройства в username
        if device_id:
            queries.append(f"@User_Name:{{{device_id}}}")

        # Поиск по MAC устройства
        if device_mac:
            escaped_mac = device_mac.replace(":", r"\:")
            queries.append(f"@Calling_Station_Id:{{{escaped_mac}}}")

        for query in queries:
            result = await execute_redis_command(
                redis, "FT.SEARCH", "idx:radius:session", query
            )

            if result and result[0] > 0:
                num_results = result[0]
                for i in range(num_results):
                    fields = result[2 + i * 2]
                    if (
                        isinstance(fields, list)
                        and len(fields) >= 2
                        and fields[0] == "$"
                    ):
                        doc_data = fields[1]
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        try:
                            session_dict = json.loads(doc_data)
                            found_session = SessionData(**session_dict)
                            session_ip = getattr(
                                found_session, "Framed_IP_Address", None
                            )
                            session_mac = getattr(
                                found_session, "Calling_Station_Id", None
                            )
                            session_username = getattr(found_session, "User_Name", None)

                            # Проверяем, что данные не совпадают с ожидаемыми
                            ip_mismatch = (
                                device_ip and session_ip and session_ip != device_ip
                            )
                            mac_mismatch = (
                                device_mac and session_mac and session_mac != device_mac
                            )
                            username_mismatch = (
                                device_id
                                and session_username
                                and session_username != device_id
                            )

                            # Если есть несоответствие, считаем это дублирующей сессией
                            if ip_mismatch or mac_mismatch or username_mismatch:
                                duplicates.append(found_session)
                                logger.debug(
                                    f"Найдена конфликтующая сессия: ID={found_session.Acct_Unique_Session_Id}, "
                                    f"IP={session_ip}(ожид.{device_ip}), MAC={session_mac}(ожид.{device_mac}), "
                                    f"User={session_username}(ожид.{device_id})"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Не удалось разобрать документ сессии устройства: {e}"
                            )
    except Exception as e:
        logger.error(f"Ошибка при поиске сессий устройства: {e}", exc_info=True)

    return duplicates


async def _kill_duplicate_sessions(sessions: list[SessionData], reason: str, rabbitmq=None) -> None:
    """Завершить список дублирующих сессий"""
    tasks = []
    for session in sessions:
        session_id = getattr(session, "Acct_Unique_Session_Id", None)
        if session_id:
            logger.info(f"Завершаем дублирующую сессию: {session_id} ({reason})")
            tasks.append(send_coa_session_kill(session.model_dump(by_alias=True), rabbitmq))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _check_and_correct_service_state(
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
    now_timestamp = datetime.now(tz=timezone.utc).timestamp()
    service_should_be_blocked = _check_service_expiry(timeto, now_timestamp)

    # Анализируем состояние с роутера
    router_says_blocked = "NOINET-NOMONEY" in session.ERX_Service_Session

    # Сравниваем состояние с роутера с ожидаемым состоянием
    if router_says_blocked and service_should_be_blocked:
        logger.debug(f"Сервис корректно заблокирован для логина {login_name}")
        return None

    elif router_says_blocked and not service_should_be_blocked:
        # Роутер заблокировал, но услуга должна работать - разблокируем
        logger.warning(
            f"Роутер неправильно заблокировал услугу для логина {login_name}, разблокировка"
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
            f"Услуга для логина {login_name} должна быть заблокирована, но роутер этого не сделал"
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
                    f"Неправильная скорость для {login_name}: "
                    f"ожидалось {expected_speed_mb} Mb, получено {service_speed_mb} Mb"
                )
                coa_attributes = {"ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)}
                await send_coa_session_set(session, rabbitmq, coa_attributes)
                return AccountingResponse(
                    action="update",
                    reason="speed mismatch corrected",
                    session_id=session.Acct_Unique_Session_Id,
                )
    return None


async def check_and_correct_services(key: str, redis=None, rabbitmq=None):
    """Проверяет и корректирует сервисы для логина или устройства"""

    if key.startswith("device:"):
        device_id = key.split(":", 1)[1]
        logger.debug(f"Проверка устройства: {device_id}")

        # Получаем данные устройства из Redis
        try:
            device_data = await search_redis(
                redis,
                query=f"@login:{{{device_id}}}",
                index="idx:device",
            )

            if not device_data:
                logger.warning(f"Данные устройства {device_id} не найдены в Redis")
                return

            device_ip = getattr(device_data, "ipAddress", None)
            device_mac = getattr(device_data, "mac", None)

            logger.debug(
                f"Данные устройства {device_id}: IP={device_ip}, MAC={device_mac}"
            )

            # Найти конфликтующие сессии
            duplicate_sessions = await _find_device_sessions_by_device_data(
                device_ip, device_mac, device_id, redis
            )

            if duplicate_sessions:
                await _kill_duplicate_sessions(
                    duplicate_sessions, f"device {device_id} IP/MAC mismatch", rabbitmq
                )
                logger.info(
                    f"Завершено {len(duplicate_sessions)} конфликтующих сессий для устройства {device_id}"
                )

        except Exception as e:
            logger.error(
                f"Ошибка при обработке устройства {device_id}: {e}", exc_info=True
            )

    elif key.startswith("login:"):
        login_name = key.split(":", 1)[1]
        logger.debug(f"Проверка сервисов для логина: {login_name}")

        # Получаем сессии логина и данные логина
        login_key = f"{RADIUS_LOGIN_PREFIX}{login_name}"
        try:
            sessions, login_data = await asyncio.gather(
                find_sessions_by_login(login_name, redis),
                search_redis(
                    redis, query=login_key, key_type="GET", redis_key=login_key
                ),
            )
        except Exception as e:
            logger.error(f"Ошибка получения данных для {login_name}: {e}")
            return

        logger.debug(f"Найдено {len(sessions)} сессий для логина {login_name}")

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
                    _find_duplicate_sessions_by_username_vlan(
                        username, vlan, unique_id, redis
                    )
                )
            else:
                # Создаем корутину, которая возвращает пустой список
                async def empty_list():
                    return []

                duplicate_search_tasks.append(empty_list())

            # Поиск дубликатов по ONU MAC
            if onu_mac:
                duplicate_search_tasks.append(
                    _find_duplicate_sessions_by_onu_mac(onu_mac, unique_id, redis)
                )
            else:
                # Создаем корутину, которая возвращает пустой список
                async def empty_list():
                    return []

                duplicate_search_tasks.append(empty_list())

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
                    await _kill_duplicate_sessions(
                        all_duplicates, "login session duplicates", rabbitmq
                    )
            except Exception as e:
                logger.error(f"Ошибка при поиске дубликатов: {e}", exc_info=True)

        # Проверяем и корректируем состояние сервисов для каждой сессии
        for session in sessions:
            try:
                correction_result = await _check_and_correct_service_state(
                    session, login_data, login_name, rabbitmq
                )
                if correction_result:
                    return correction_result
            except Exception as e:
                logger.error(
                    f"Ошибка при проверке сервиса для сессии {session.Acct_Unique_Session_Id}: {e}",
                    exc_info=True,
                )

    else:
        logger.warning(f"Неизвестный тип ключа для проверки сервисов: {key}")
        raise HTTPException(
            status_code=400, detail="Unknown key type for service check"
        )
