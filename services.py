import re
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from schemas import AccountingData, AccountingResponse
from redis_client import get_redis
from rabbitmq_client import rmq_send_message
from utils import (
    parse_event,
    find_login_by_session,
    enrich_session_with_login,
    process_traffic_data,
)
from config import (
    RADIUS_SESSION_PREFIX,
    SESSION_TEMPLATE,
    AMQP_SESSION_QUEUE,
    AMQP_TRAFFIC_QUEUE,
)
import metrics

logger = logging.getLogger(__name__)


async def save_session_to_redis(session_data: Dict[str, Any], redis_key: str) -> bool:
    """Сохранение сессии в Redis"""
    try:
        redis = await get_redis()
        await redis.set(redis_key, json.dumps(session_data, default=str))
        # Устанавливаем TTL на 24 часа
        await redis.expire(redis_key, 86400)
        logger.debug(f"Session saved to Redis: {redis_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to save session to Redis: {e}")
        return False


async def delete_session_from_redis(redis_key: str) -> bool:
    """Удаление сессии из Redis"""
    try:
        redis = await get_redis()
        result = await redis.delete(redis_key)
        logger.debug(f"Session deleted from Redis: {redis_key}, result: {result}")
        return result > 0
    except Exception as e:
        logger.error(f"Failed to delete session from Redis: {e}")
        return False


async def get_session_from_redis(redis_key: str) -> Optional[Dict[str, Any]]:
    """Получение сессии из Redis"""
    redis = await get_redis()
    try:
        session_data = await redis.execute_command("JSON.GET", redis_key)
        if session_data:
            return json.loads(session_data)
        return None
    except Exception as e:
        logger.error(f"Failed to get session from RedisJSON: {e}")
        return None


async def send_coa_session_kill(session_req: Dict[str, Any]) -> bool:
    """Отправка команды на убийство сессии через CoA"""
    ...


async def send_coa_session_set(
    session_req: Dict[str, Any], attributes: Dict[str, Any]
) -> bool:
    """Отправка команды на обновление атрибутов сессии через CoA"""
    ...


async def process_accounting(data: AccountingData) -> AccountingResponse:
    """Основная функция обработки аккаунтинга"""
    start_time = time.time()
    status = "success"

    try:
        # Конвертируем в словарь
        session_req = data.dict(exclude_none=True)
        packet_type = session_req.get("Acct_Status_Type", "UNKNOWN")
        session_unique_id = session_req.get("Acct_Unique_Session_Id")

        logger.info(
            f"Processing accounting packet: {packet_type} for session {session_unique_id}"
        )

        # Записываем метрики
        metrics.record_packet("accounting", "received", packet_type)
        metrics.record_accounting_packet(packet_type, "UNKNOWN")

        # Валидация обязательных полей
        if not session_unique_id:
            logger.error("Missing Acct-Unique-Session-Id")
            metrics.record_error("missing_session_id", "accounting")
            return AccountingResponse(
                action="log", reason="missing session id", status="error"
            )

        event_timestamp_str = session_req.get("Event_Timestamp")
        if not event_timestamp_str:
            logger.error("Missing Event-Timestamp")
            metrics.record_error("missing_event_timestamp", "accounting")
            return AccountingResponse(
                action="log", reason="missing event timestamp", status="error"
            )

        event_timestamp = parse_event(event_timestamp_str)

        # Получаем активную сессию из Redis
        redis_key = f"{RADIUS_SESSION_PREFIX}{session_unique_id}"
        session_stored = await get_session_from_redis(redis_key)

        # Ищем логин
        login = await find_login_by_session(session_req)
        if login and isinstance(login, dict):
            session_req = enrich_session_with_login(session_req, login)
        else:
            logger.warning("Login not found or invalid")
            session_req = enrich_session_with_login(session_req, {})

        # Обрабатываем счетчики трафика
        session_req = process_traffic_data(session_req)

        is_service_session = bool(session_req.get("ERX_Service_Session"))
        service = session_req.get("ERX_Service_Session", "")

        # Обработка сервисной сессии
        if is_service_session and login and isinstance(login, dict):
            servicecats = login.get("servicecats", {})
            if isinstance(servicecats, dict):
                internet_service = servicecats.get("internet", {})
                if isinstance(internet_service, dict):
                    timeto = internet_service.get("timeto")
                    speed = internet_service.get("speed", 0)

                    # Проверка срока действия услуги
                    if timeto is not None:
                        try:
                            timeto_float = float(timeto)
                            if datetime.fromtimestamp(
                                timeto_float, tz=timezone.utc
                            ) < datetime.now(tz=timezone.utc):
                                logger.warning(
                                    f"Service expired for login {login.get('login', 'unknown')}"
                                )
                                await send_coa_session_kill(session_req)
                                return AccountingResponse(
                                    action="kill",
                                    reason="service expired",
                                    session_id=session_unique_id,
                                )
                        except (ValueError, TypeError) as e:
                            logger.error(f"Invalid timeto value: {timeto}, error: {e}")

                    # Проверка соответствия скорости
                    match = re.search(r"\(([\d.]+k)\)", service)
                    if match:
                        service_speed_mb = (
                            float(match.group(1).replace("k", "")) / 1000
                        )  # k -> Mb
                        expected_speed_mb = float(speed) * 1.1 if speed else 0
                        if abs(service_speed_mb - expected_speed_mb) >= 0.01:
                            logger.warning(
                                f"Speed mismatch: expected {expected_speed_mb} Mb, got {service_speed_mb} Mb"
                            )
                            # Обновляем параметры сессии
                            coa_attributes = {
                                "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
                            }
                            await send_coa_session_set(session_req, coa_attributes)
                            return AccountingResponse(
                                action="update",
                                reason="speed mismatch corrected",
                                coa_attributes=coa_attributes,
                                session_id=session_unique_id,
                            )
                    elif "NOINET-NOMONEY" in service:
                        logger.warning(
                            f"Service blocked for login {login.get('login', 'unknown')}"
                        )
                        await send_coa_session_kill(session_req)
                        return AccountingResponse(
                            action="kill",
                            reason="service blocked",
                            session_id=session_unique_id,
                        )

        # Создаем новую сессию на основе шаблона
        session_new = SESSION_TEMPLATE.copy()
        session_new.update(session_req)

        # Проверка смены логина
        if (
            session_stored
            and login
            and isinstance(login, dict)
            and session_stored.get("login") != login.get("login")
        ):
            logger.info(
                f"Login changed from {session_stored.get('login', 'unknown')} "
                f"to {login.get('login', 'unknown')}, killing session"
            )
            await send_coa_session_kill(session_stored)

        # Обработка по типу пакета
        if packet_type == "Start":
            logger.info("Processing START packet")
            session_new["Acct-Start-Time"] = event_timestamp
            session_new["Acct-Session-Time"] = 0

            await save_session_to_redis(session_new, redis_key)
            await ch_save_session(session_new)

        elif packet_type == "Interim-Update":
            if session_stored:
                logger.info("Processing Interim-Update for existing session")
                session_new = session_stored | session_req
                session_new["Acct-Session-Time"] = int(
                    session_req.get("Acct_Session_Time", 0)
                )

                if not is_service_session:
                    await save_session_to_redis(session_new, redis_key)
                await ch_save_session(session_new)
                await ch_save_traffic(session_new, session_stored)
            else:
                logger.warning(
                    "Interim-Update without existing session, creating new one"
                )
                session_new["Acct-Start-Time"] = event_timestamp - int(
                    session_req.get("Acct_Session_Time", 0)
                )
                session_new["Acct-Session-Time"] = int(
                    session_req.get("Acct_Session_Time", 0)
                )

                if not is_service_session:
                    await save_session_to_redis(session_new, redis_key)
                await ch_save_session(session_new)
                await ch_save_traffic(session_new, session_stored)

        elif packet_type == "Stop":
            logger.info("Processing STOP packet")
            session_new["Acct-Start-Time"] = (
                event_timestamp - int(session_req.get("Acct_Session_Time", 0))
                if not session_stored
                else session_stored.get("Acct-Start-Time", event_timestamp)
            )
            session_new["Acct-Session-Time"] = int(
                session_req.get("Acct_Session_Time", 0)
            )
            session_new["Acct_Terminate_Cause"] = session_req.get(
                "Acct_Terminate_Cause"
            )

            await ch_save_session(session_new)
            await ch_save_traffic(session_new, session_stored)

            if session_stored:
                await delete_session_from_redis(redis_key)

        else:
            logger.error(f"Unknown packet type: {packet_type}")
            return AccountingResponse(
                action="log",
                reason=f"unknown packet type: {packet_type}",
                status="error",
            )

        logger.info(f"Accounting packet processed successfully: {packet_type}")
        return AccountingResponse(
            action="noop", reason="processed successfully", session_id=session_unique_id
        )

    except Exception as e:
        status = "error"
        logger.error(f"Error processing accounting: {e}", exc_info=True)
        metrics.record_error("accounting_exception", "accounting")
        return AccountingResponse(
            action="log", reason=f"processing error: {str(e)}", status="error"
        )
    finally:
        exec_time = time.time() - start_time
        metrics.record_operation_duration("accounting", exec_time, status)
        logger.debug(f"Accounting processing took {exec_time:.3f}s, status: {status}")


async def ch_save_session(session_data: Dict[str, Any], stoptime: bool = False) -> bool:
    """Сохранение сессии в ClickHouse через RabbitMQ"""
    start_time = time.time()
    status = "success"
    logger.info(
        f"Сохранение сессии в ClickHouse: {session_data.get('Acct_Unique_Session_Id')}"
    )

    try:
        columns = [
            "login",
            "onu_mac",
            "contract",
            "packet_type",
            "auth_type",
            "Acct-Status-Type",
            "service",
            "Acct-Session-Id",
            "Acct-Unique-Session-Id",
            "Acct-Start-Time",
            "Acct-Stop-Time",
            "User-Name",
            "NAS-IP-Address",
            "NAS-Port-Id",
            "NAS-Port-Type",
            "Calling-Station-Id",
            "Acct-Terminate-Cause",
            "Service-Type",
            "Framed-Protocol",
            "Framed-IP-Address",
            "Framed-IPv6-Prefix",
            "Delegated-IPv6-Prefix",
            "Acct-Session-Time",
            "Acct-Input-Octets",
            "Acct-Output-Octets",
            "Acct-Input-Packets",
            "Acct-Output-Packets",
            "ERX-IPv6-Acct-Input-Octets",
            "ERX-IPv6-Acct-Output-Octets",
            "ERX-IPv6-Acct-Input-Packets",
            "ERX-IPv6-Acct-Output-Packets",
            "ERX-IPv6-Acct-Input-Gigawords",
            "ERX-IPv6-Acct-Output-Gigawords",
            "ERX-Virtual-Router-Name",
            "ERX-Service-Session",
            "ADSL-Agent-Circuit-Id",
            "ADSL-Agent-Remote-Id",
        ]

        # Копируем данные сессии для обработки
        session = session_data.copy()

        # Обработка времени начала сессии
        if isinstance(session.get("Acct-Start-Time"), (int, float)):
            session["Acct-Start-Time"] = datetime.fromtimestamp(
                session["Acct-Start-Time"], tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")

        event_timestamp_str = session.get("Event_Timestamp")
        if not stoptime:
            # Активная сессия, не заполняем Stop-Time
            logger.debug("Активная сессия, Acct-Stop-Time будет пустым")
            session["Acct-Stop-Time"] = None
            session["Acct-Update-Time"] = datetime.now(tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            if event_timestamp_str:
                event_timestamp = parse_event(event_timestamp_str)
                session["Acct-Stop-Time"] = datetime.fromtimestamp(
                    event_timestamp, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S")
                session["Acct-Update-Time"] = datetime.fromtimestamp(
                    event_timestamp, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S")
                logger.debug(
                    f"Сессия будет остановлена: {session.get('Acct_Unique_Session_Id')}"
                )
            else:
                logger.debug(
                    f"Сессия будет остановлена без Event-Timestamp: {session.get('Acct_Unique_Session_Id')}"
                )
                session["Acct-Stop-Time"] = datetime.now(tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                session["Acct-Update-Time"] = session["Acct-Stop-Time"]

        # Обеспечиваем правильное форматирование всех полей
        session["Framed-Protocol"] = session.get("Framed-Protocol", "")
        session["Framed-IPv6-Prefix"] = session.get("Framed_IPv6_Prefix", "")
        session["Delegated-IPv6-Prefix"] = session.get("Delegated_IPv6_Prefix", "")
        session["ERX-Service-Session"] = session.get("ERX_Service_Session", "")
        session["ADSL-Agent-Circuit-Id"] = session.get("ADSL_Agent_Circuit_Id", "")
        session["ADSL-Agent-Remote-Id"] = session.get("ADSL_Agent_Remote_Id", "")
        session["packet_type"] = session.get("Acct_Status_Type")

        session["ERX-IPv6-Acct-Input-Gigawords"] = session.get(
            "ERX-IPv6-Acct-Input-Gigawords", 0
        )
        session["ERX-IPv6-Acct-Output-Gigawords"] = session.get(
            "ERX-IPv6-Acct-Output-Gigawords", 0
        )

        row = [session.get(x) for x in columns]
        columns.append("GMT")
        row.append(5)

        result = await rmq_send_message(AMQP_SESSION_QUEUE, session)
        if result:
            logger.info(
                f"Сессия отправлена в очередь session_queue: {session.get('Acct_Unique_Session_Id')}"
            )
        return result
    except Exception as e:
        status = "error"
        logger.error(
            f"Ошибка при сохранении сессии {session_data.get('Acct_Unique_Session_Id')}: {e}"
        )
        metrics.record_error("clickhouse_save_session_error", "ch_save_session")
        return False
    finally:
        exec_time = time.time() - start_time
        metrics.record_operation_duration("ch_save_session", exec_time, status)


async def ch_save_traffic(
    session_new: Dict[str, Any], session_stored: Optional[Dict[str, Any]] = None
) -> bool:
    """Сохранение трафика в ClickHouse через RabbitMQ"""
    start_time = time.time()
    status = "success"

    try:
        columns = [
            "Acct_Input_Octets",
            "Acct_Output_Octets",
            "Acct_Input_Packets",
            "Acct_Output_Packets",
            "ERX-IPv6-Acct-Input-Octets",
            "ERX-IPv6-Acct-Output-Octets",
            "ERX-IPv6-Acct-Input-Packets",
            "ERX-IPv6-Acct-Output-Packets",
        ]

        traffic_data = {
            "Acct-Unique-Session-Id": session_new.get("Acct_Unique_Session_Id", ""),
            "login": session_new.get("login", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if session_stored:
            logger.debug(
                f"Вычисляем дельту трафика для сессии: {session_new.get('Acct_Unique_Session_Id')}"
            )
            negative_deltas = []
            for col in columns:
                new_val = session_new.get(col, 0) or 0
                stored_val = session_stored.get(col, 0) or 0
                delta = new_val - stored_val
                traffic_data[col] = delta
                if delta < 0:
                    negative_deltas.append(
                        f"{col}: {stored_val} -> {new_val}, Δ={delta}"
                    )

            if negative_deltas:
                logger.error(
                    f"Отрицательная дельта трафика для {session_new.get('Acct_Unique_Session_Id')}: {'; '.join(negative_deltas)}"
                )
        else:
            logger.debug(
                f"Сохраняем полный трафик новой сессии: {session_new.get('Acct_Unique_Session_Id')}"
            )
            for col in columns:
                traffic_data[col] = session_new.get(col, 0) or 0

        total_input = traffic_data.get("Acct_Input_Octets", 0)
        total_output = traffic_data.get("Acct_Output_Octets", 0)
        total_traffic = total_input + total_output

        if total_traffic > 0:
            logger.debug(
                f"Трафик для {session_new.get('Acct_Unique_Session_Id')}: "
                f"вход={total_input}, выход={total_output}, всего={total_traffic} байт"
            )

        result = await rmq_send_message(AMQP_TRAFFIC_QUEUE, traffic_data)
        if result:
            logger.info(
                f"Трафик отправлен в очередь traffic_queue: {session_new.get('Acct_Unique_Session_Id')}"
            )
        return result
    except Exception as e:
        status = "error"
        logger.error(
            f"Ошибка сохранения трафика для {session_new.get('Acct_Unique_Session_Id')}: {e}"
        )
        metrics.record_error("clickhouse_save_traffic_error", "ch_save_traffic")
        return False
    finally:
        exec_time = time.time() - start_time
        metrics.record_operation_duration("ch_save_traffic", exec_time, status)
