import json
import logging
import re
import time
from typing import Any, Dict, Optional

from pydantic import ValidationError

from config import (
    AMQP_AUTH_LOG_QUEUE,
    AMQP_SESSION_QUEUE,
    AMQP_TRAFFIC_QUEUE,
    RADIUS_LOGIN_PREFIX,
    RADIUS_SESSION_PREFIX,
)

from datetime import datetime, timezone

from rabbitmq_client import rmq_send_message
from redis_client import (
    execute_redis_command,
    execute_redis_pipeline,
    get_redis,
)
from schemas import (
    AccountingData,
    AuthDataLog,
    AuthRequest,
    EnrichedSessionData,
    LoginSearchResult,
    SessionData,
    TrafficData,
)
from utils import (
    is_mac_username,
    mac_from_hex,
    mac_from_username,
    nasportid_parse,
    now_str,
)

logger = logging.getLogger(__name__)


async def get_session_from_redis(redis_key: str, redis=None) -> Optional[SessionData]:
    """
    Получение сессии из Redis и преобразование в модель RedisSessionData.
    """
    if redis is None:
        redis = await get_redis()
    try:
        session_data = await execute_redis_command(redis, "JSON.GET", redis_key)
        if not session_data:
            logger.debug(f"No session data found for key: {redis_key}")
            return None

        parsed_data = json.loads(session_data)
        session = SessionData(**parsed_data)
        logger.debug(f"Successfully retrieved session for key: {redis_key}")
        return session

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON for key {redis_key}: {e}")
        return None
    except ValidationError as e:
        logger.error(f"Invalid session data for key {redis_key}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to get session from Redis for key {redis_key}: {e}")
        return None


async def enrich_session_with_login(
    session_req: AccountingData, login: Optional[LoginSearchResult]
) -> EnrichedSessionData:
    """
    Обогащение данных сессии информацией о логине.

    Args:
        session_req: Данные сессии (AccountingData).
        login: Данные логина (LoginSearchResult) или None.

    Returns:
        EnrichedSessionData: Обогащенная модель сессии с данными логина.
    """
    session_dict = session_req.model_dump(by_alias=True)

    if login:
        session_dict.update(login.model_dump(by_alias=True))

    if session_dict.get("ERX-Service-Session"):
        session_dict["service"] = session_dict["ERX-Service-Session"]

    try:
        return EnrichedSessionData(**session_dict)
    except ValidationError as e:
        logger.error(f"Failed to create EnrichedSessionData: {e}")
        accounting_fields = AccountingData.model_fields.keys()
        return EnrichedSessionData(
            **{k: v for k, v in session_dict.items() if k in accounting_fields}
        )


async def process_traffic_data(session_req: EnrichedSessionData) -> EnrichedSessionData:
    """Преобразование данных трафика для сессии."""
    if session_req.Acct_Status_Type == "Start":
        return session_req
    session_req.Acct_Input_Octets = (
        session_req.Acct_Input_Gigawords << 32
    ) | session_req.Acct_Input_Octets
    session_req.Acct_Output_Octets = (
        session_req.Acct_Output_Gigawords << 32
    ) | session_req.Acct_Output_Octets
    session_req.Acct_Input_Packets = (
        session_req.ERX_Input_Gigapkts << 32
    ) | session_req.Acct_Input_Packets
    session_req.Acct_Output_Packets = (
        session_req.ERX_Output_Gigapkts << 32
    ) | session_req.Acct_Output_Packets
    session_req.ERX_IPv6_Acct_Input_Octets = (
        session_req.ERX_IPv6_Acct_Input_Gigawords << 32
    ) | session_req.ERX_IPv6_Acct_Input_Octets
    session_req.ERX_IPv6_Acct_Output_Octets = (
        session_req.ERX_IPv6_Acct_Output_Gigawords << 32
    ) | session_req.ERX_IPv6_Acct_Output_Octets
    return session_req


async def save_session_to_redis(
    session_data: SessionData, redis_key: str, redis=None
) -> bool:
    """Сохранение сессии в Redis с оптимизацией через pipeline"""
    try:
        # Используем pipeline для выполнения двух команд за один раз
        commands = [
            ("JSON.SET", redis_key, "$", session_data.model_dump_json(by_alias=True)),
            ("EXPIRE", redis_key, 1800),  # TTL на 30 минут
        ]
        await execute_redis_pipeline(commands, redis_client=redis)
        logger.debug(f"Session saved to RedisJSON with TTL: {redis_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to save session to RedisJSON: {e}")
        return False


async def delete_session_from_redis(redis_key: str, redis=None) -> bool:
    """Удаление сессии из Redis"""
    try:
        if redis is None:
            redis = await get_redis()
        result = await execute_redis_command(redis, "DEL", redis_key)
        logger.debug(f"Session deleted from Redis: {redis_key}, result: {result}")
        return result > 0
    except Exception as e:
        logger.error(f"Failed to delete session from Redis: {e}")
        return False


async def search_redis(
    redis,
    query: str,
    auth_type: Optional[str] = None,
    key_type: str = "FT.SEARCH",
    index: str = "idx:radius:login",
    redis_key: Optional[str] = None,
) -> Optional[LoginSearchResult]:
    """
    Универсальный поиск в Redis по заданному запросу или ключу.

    Args:
        redis: Асинхронный Redis-клиент.
        query: Запрос для FT.SEARCH или ключ для redis.get.
        auth_type: Тип аутентификации (MAC, OPT82, STATIC, PPPOE).
        key_type: Тип операции ('FT.SEARCH' или 'GET'). По умолчанию 'FT.SEARCH'.
        index: Индекс для FT.SEARCH. По умолчанию 'idx:radius:login'.
        redis_key: Ключ для redis.get (используется, если key_type='GET').

    Returns:
        Optional[LoginSearchResult]: Результат поиска или None в случае ошибки.
    """
    try:
        if key_type == "FT.SEARCH":
            result = await execute_redis_command(redis, "FT.SEARCH", index, query)
            if not result or result[0] == 0:
                logger.debug(f"No results for {key_type} query: {query}")
                return None
            doc_data = result[2][1]
            if isinstance(doc_data, bytes):
                doc_data = doc_data.decode("utf-8")
            parsed_data = json.loads(doc_data)
        elif key_type == "GET":
            if not redis_key:
                logger.error("redis_key is required for GET operation")
                return None
            result = await execute_redis_command(redis, "JSON.GET", redis_key)
            if not result:
                logger.debug(f"No data found for key: {redis_key}")
                return None
            parsed_data = (
                json.loads(result) if isinstance(result, (str, bytes)) else result
            )
        else:
            logger.error(f"Unsupported key_type: {key_type}")
            return None

        if auth_type:
            parsed_data["auth_type"] = auth_type
        login_data = LoginSearchResult(**parsed_data)
        logger.debug(f"Found login: {login_data.login} (auth_type: {auth_type})")
        return login_data

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON for {key_type} query {query}: {e}")
        return None
    except ValidationError as e:
        logger.error(f"Invalid data for {key_type} query {query}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error executing {key_type} query {query}: {e}")
        return None


async def find_login_by_session(
    session: AccountingData | AuthRequest,
    redis=None,
) -> Optional[LoginSearchResult]:
    """
    Асинхронный поиск логина по данным сессии.

    Args:
        session: Данные сессии (AccountingData).
        redis: Redis client, если не передан - создается новый

    Returns:
        Optional[LoginSearchResult]: Результат поиска или None, если логин не найден.
    """
    start_time = time.time()
    if redis is None:
        redis = await get_redis()

    try:
        nas_port_id = session.NAS_Port_Id
        if not nas_port_id:
            logger.warning("Missing NAS-Port-Id in session")
            return None

        nasportid = nasportid_parse(nas_port_id)
        vlan = nasportid.get("cvlan") or nasportid.get("svlan", "")
        if not vlan:
            logger.warning(f"Could not extract VLAN from NAS-Port-Id: {nas_port_id}")
            return None

        username = session.User_Name
        if not username:
            logger.warning("Missing User-Name in session")
            return None

        logger.debug(f"Searching login: username={username}, VLAN={vlan}")

        if is_mac_username(username):
            logger.debug(f"IPoE session, MAC username: {username}")
            mac = mac_from_username(username).replace(":", r"\:")

            # Поиск логина по МАКу
            search_query = f"@mac:{{{mac}}}@vlan:{{{vlan}}}"
            result = await search_redis(redis, search_query, auth_type="MAC")
            if result:
                return result

            # Поиск камеры по МАКу
            search_query = f"@mac:{{{mac}}}"
            result = await search_redis(
                redis, search_query, auth_type="VIDEO", index="idx:device"
            )
            if result:
                return result

            remote_id = session.ADSL_Agent_Remote_Id
            if remote_id:
                onu_mac = mac_from_hex(remote_id).replace(":", r"\:")
                search_query = f"@onu_mac:{{{onu_mac}}}"
                result = await search_redis(redis, search_query, auth_type="OPT82")
                if result:
                    return result

        else:
            logger.debug(f"PPPoE/static session, username: {username}")
            static_match = re.match(r"^static-(.+)", username)
            if static_match:
                ip = static_match.groups()[0]
                logger.debug(f"Static session, IP: {ip}")
                escaped_ip = ip.replace(".", "\\.")
                search_query = f"@ip_addr:{{{escaped_ip}}}@vlan:{{{vlan}}}"
                result = await search_redis(redis, search_query, auth_type="STATIC")
                if result:
                    return result
            else:
                login_key = f"{RADIUS_LOGIN_PREFIX}{username.strip().lower()}"
                result = await search_redis(
                    redis,
                    query=login_key,
                    auth_type="PPPOE",
                    key_type="GET",
                    redis_key=login_key,
                )
                if result:
                    return result

        logger.info(f"Login not found: username={username}, VLAN={vlan}")
        return None

    except Exception as e:
        logger.error(f"Critical error in find_login_by_session: {e}")
        return None
    finally:
        exec_time = time.time() - start_time
        logger.debug(f"Login search took {exec_time:.3f}s")


async def find_sessions_by_login(login: str, redis) -> list[SessionData]:
    """Найти и вернуть массив всех сессий по логину."""
    escaped_login = login.replace("-", r"\-")
    query = f"@login:{{{escaped_login}}}"
    index = "idx:radius:session"

    try:
        result = await execute_redis_command(
            redis, "FT.SEARCH", index, query, "LIMIT", 0, 10000
        )

        if not result or result[0] == 0:
            return []

        sessions = []  # Результат: [count, key, fields] для одного документа
        # Для нескольких: [count, key1, fields1, key2, fields2, ...]
        num_results = result[0]

        for i in range(num_results):
            # Индекс полей: 2 + i*2
            fields_index = 2 + i * 2

            if fields_index < len(result):
                fields = result[fields_index]

                # fields = ['$', 'json_string']
                if isinstance(fields, list) and len(fields) >= 2 and fields[0] == "$":
                    json_data = fields[1]

                    try:
                        if isinstance(json_data, bytes):
                            json_data = json_data.decode("utf-8")

                        session_dict = json.loads(json_data)
                        sessions.append(SessionData(**session_dict))
                    except Exception as e:
                        logger.warning(f"Failed to parse session data: {e}")

        return sessions

    except Exception as e:
        logger.error(f"Error searching sessions for login '{login}': {e}")
        return []


async def ch_save_session(session_data: SessionData, stoptime: bool = False) -> bool:
    """Сохранение сессии в ClickHouse через RabbitMQ"""
    logger.info(
        f"Сохранение сессии в ClickHouse: {session_data.Acct_Unique_Session_Id}"
    )

    try:
        # Убеждаемся что все времена в UTC формате
        if session_data.Event_Timestamp:
            # Приводим к UTC если нужно
            if session_data.Event_Timestamp.tzinfo is None:
                session_data.Event_Timestamp = session_data.Event_Timestamp.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Event_Timestamp = session_data.Event_Timestamp.astimezone(
                    timezone.utc
                )

        # Устанавливаем Acct_Update_Time если он еще не установлен
        if not session_data.Acct_Update_Time:
            session_data.Acct_Update_Time = (
                session_data.Event_Timestamp or datetime.now(tz=timezone.utc)
            )

        # Приводим Acct_Update_Time к UTC
        if session_data.Acct_Update_Time.tzinfo is None:
            session_data.Acct_Update_Time = session_data.Acct_Update_Time.replace(
                tzinfo=timezone.utc
            )
        else:
            session_data.Acct_Update_Time = session_data.Acct_Update_Time.astimezone(
                timezone.utc
            )

        # Приводим Acct_Start_Time к UTC если он есть
        if session_data.Acct_Start_Time:
            if session_data.Acct_Start_Time.tzinfo is None:
                session_data.Acct_Start_Time = session_data.Acct_Start_Time.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Acct_Start_Time = session_data.Acct_Start_Time.astimezone(
                    timezone.utc
                )

        # Обрабатываем Acct_Stop_Time
        if stoptime:
            if not session_data.Acct_Stop_Time:
                session_data.Acct_Stop_Time = (
                    session_data.Event_Timestamp or datetime.now(tz=timezone.utc)
                )
            # Приводим к UTC
            if session_data.Acct_Stop_Time.tzinfo is None:
                session_data.Acct_Stop_Time = session_data.Acct_Stop_Time.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Acct_Stop_Time = session_data.Acct_Stop_Time.astimezone(
                    timezone.utc
                )
            logger.debug(
                f"Сессия будет остановлена: {session_data.Acct_Unique_Session_Id}"
            )
        else:
            # Активная сессия, не заполняем Stop-Time
            logger.debug("Активная сессия, Acct-Stop-Time будет пустым")
            session_data.Acct_Stop_Time = None

        result = await rmq_send_message(AMQP_SESSION_QUEUE, session_data)
        if result:
            logger.info(
                f"Сессия отправлена в очередь session_queue: {session_data.Acct_Unique_Session_Id}"
            )
        return result
    except Exception as e:
        logger.error(
            f"Ошибка при сохранении сессии {session_data.Acct_Unique_Session_Id}: {e}"
        )
        raise


async def ch_save_traffic(
    session_new: SessionData, session_stored: Optional[SessionData] = None
) -> bool:
    """Сохранение трафика в ClickHouse через RabbitMQ"""

    try:
        # Определяем поля трафика и их алиасы
        traffic_fields = [
            ("Acct_Input_Octets", "Acct-Input-Octets"),
            ("Acct_Output_Octets", "Acct-Output-Octets"),
            ("Acct_Input_Packets", "Acct-Input-Packets"),
            ("Acct_Output_Packets", "Acct-Output-Packets"),
            ("ERX_IPv6_Acct_Input_Octets", "ERX-IPv6-Acct-Input-Octets"),
            ("ERX_IPv6_Acct_Output_Octets", "ERX-IPv6-Acct-Output-Octets"),
            ("ERX_IPv6_Acct_Input_Packets", "ERX-IPv6-Acct-Input-Packets"),
            ("ERX_IPv6_Acct_Output_Packets", "ERX-IPv6-Acct-Output-Packets"),
        ]

        # Базовые данные с алиасами
        traffic_data: Dict[str, Any] = {
            "Acct-Unique-Session-Id": session_new.Acct_Unique_Session_Id,
            "login": session_new.login,
            "timestamp": now_str(),
        }

        # Добавляем трафик (дельта или полный) с алиасами
        negative_deltas = []
        for field, alias in traffic_fields:
            new_val = getattr(session_new, field, 0) or 0

            if session_stored:
                stored_val = getattr(session_stored, field, 0) or 0
                delta = new_val - stored_val
                if delta < 0:
                    negative_deltas.append(
                        f"{field}: {stored_val} -> {new_val}, Δ={delta}"
                    )
                    delta = 0
                traffic_data[alias] = delta
            else:
                traffic_data[alias] = new_val

        if negative_deltas:
            logger.error(
                f"Отрицательная дельта трафика для {session_new.Acct_Unique_Session_Id}: "
                f"; ".join(negative_deltas)
            )

        traffic_model = TrafficData(**traffic_data)

        # Отправляем в RabbitMQ
        result = await rmq_send_message(AMQP_TRAFFIC_QUEUE, traffic_model)

        if result:
            action = "дельта" if session_stored else "полный"
            logger.info(
                f"Трафик ({action}) отправлен в очередь: {session_new.Acct_Unique_Session_Id}"
            )

        return result

    except Exception as e:
        logger.error(
            f"Ошибка сохранения трафика для {session_new.Acct_Unique_Session_Id}: {e}"
        )
        return False


async def save_auth_log_to_queue(auth_data: AuthDataLog) -> bool:
    logger.debug(f"Сохранение лога авторизации: {auth_data.username}")
    try:
        result = await rmq_send_message(AMQP_AUTH_LOG_QUEUE, auth_data)
        if result:
            logger.info(f"Лог авторизации отправлен в очередь: {auth_data.username}")
        return result
    except Exception as e:
        logger.error(
            f"Ошибка сохранения лога авторизации для {auth_data.username}: {e}"
        )
        return False


async def update_main_session_service(
    service_session_req: EnrichedSessionData, redis=None
) -> bool:
    """
    Обновляет поле ERX-Service-Session в основной сессии на основе данных сервисной сессии.
    Args:
        service_session_req: Данные сервисной сессии
        redis: Redis client, если не передан - создается новый
    Returns:
        bool: True если обновление прошло успешно, False если основная сессия не найдена
    """
    try:
        service_session_id = service_session_req.Acct_Session_Id
        if redis is None:
            redis = await get_redis()

        # Извлекаем основной ID из сервисного (берем часть до двоеточия)
        if ":" in service_session_id:
            main_session_id = service_session_id.split(":")[0]
        else:
            logger.warning(
                f"Неожиданный формат ID сервисной сессии: {service_session_id}"
            )
            return False

        logger.debug(
            f"Поиск основной сессии по Acct-Session-Id: {service_session_id} -> {main_session_id}"
        )

        # Поиск основной сессии по полю Acct-Session-Id в индексе
        index = "idx:radius:session"
        query = f"@Acct\\-Session\\-Id:{{{main_session_id}}}"
        logger.debug(f"Поиск основной сессии в индексе {index} с запросом {query}")

        # Получаем основную сессию из Redis
        result = await execute_redis_command(redis, "FT.SEARCH", index, query)

        logger.debug(
            f"FT.SEARCH result for main_session_id={main_session_id}: {result}"
        )

        if result and result[0] > 0:
            num_results = result[0]
            logger.info(
                f"Найдено {num_results} сессий по Acct-Session-Id {main_session_id}"
            )
            for i in range(num_results):
                try:
                    fields = result[2 + i * 2]
                    logger.debug(f"fields[{i}]: {fields}")
                    if (
                        isinstance(fields, list)
                        and len(fields) >= 2
                        and fields[0] == "$"
                    ):
                        doc_data = fields[1]
                        logger.debug(f"doc_data[{i}]: {doc_data}")
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        logger.debug(f"doc_data[{i}] (decoded): {doc_data}")
                        session_dict = json.loads(doc_data)
                        logger.debug(f"session_dict[{i}]: {session_dict}")
                        main_session = SessionData(**session_dict)
                        logger.info(
                            f"Основная сессия для обновления [{i}]: {main_session}"
                        )
                        # Обновляем поле ERX-Service-Session в основной сессии
                        main_session.ERX_Service_Session = (
                            service_session_req.ERX_Service_Session
                        )
                        # Сохраняем обновленную основную сессию обратно в Redis
                        main_redis_key = f"{RADIUS_SESSION_PREFIX}{main_session.Acct_Unique_Session_Id}"
                        await save_session_to_redis(main_session, main_redis_key)
                        logger.info(
                            f"Обновлено поле ERX-Service-Session в основной сессии {main_session_id}: "
                            f"{service_session_req.ERX_Service_Session}"
                        )
                        return True
                    else:
                        logger.warning(
                            f"fields[{i}] не содержит ожидаемых данных: {fields}"
                        )
                except Exception as e:
                    logger.error(
                        f"Ошибка при обработке результата FT.SEARCH для основной сессии [{i}]: {e}",
                        exc_info=True,
                    )
            logger.warning(
                f"Не удалось найти и обновить основную сессию с Acct-Session-Id {main_session_id} после перебора всех результатов. Итоговый result: {result}"
            )
            return False
        else:
            logger.warning(
                f"Основная сессия с Acct-Session-Id {main_session_id} не найдена в Redis. FT.SEARCH result: {result}"
            )
            return False

    except Exception as e:
        logger.error(f"Ошибка при обновлении основной сессии: {e}", exc_info=True)
        return False
