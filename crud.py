import time
import re
import json
import logging
from typing import Optional

from pydantic import ValidationError
from redis_client import get_redis
from config import RADIUS_LOGIN_PREFIX
from schemas import AccountingData, AuthRequest, EnrichedSessionData, LoginSearchResult, SessionData
from utils import is_mac_username, mac_from_hex, mac_from_username, nasportid_parse

logger = logging.getLogger(__name__)


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


async def save_session_to_redis(session_data: SessionData, redis_key: str) -> bool:
    """Сохранение сессии в Redis"""
    try:
        redis = await get_redis()
        # Сохраняем сессию в формате RedisJSON с алиасами (дефисами)
        await redis.execute_command(
            "JSON.SET", redis_key, "$", session_data.model_dump_json(by_alias=True)
        )
        # Устанавливаем TTL на 30 минут
        await redis.expire(redis_key, 1800)
        logger.debug(f"Session saved to RedisJSON: {redis_key}")
        return True
    except Exception as e:
        logger.error(f"Failed to save session to RedisJSON: {e}")
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


async def search_redis(
    redis,
    query: str,
    auth_type: str,
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
            result = await redis.execute_command("FT.SEARCH", index, query)
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
            result = await redis.execute_command("JSON.GET", redis_key)
            if not result:
                logger.debug(f"No data found for key: {redis_key}")
                return None
            parsed_data = (
                json.loads(result) if isinstance(result, (str, bytes)) else result
            )
        else:
            logger.error(f"Unsupported key_type: {key_type}")
            return None

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


async def find_login_by_session(session: AccountingData | AuthRequest) -> Optional[LoginSearchResult]:
    """
    Асинхронный поиск логина по данным сессии.

    Args:
        session: Данные сессии (AccountingData).

    Returns:
        Optional[LoginSearchResult]: Результат поиска или None, если логин не найден.
    """
    start_time = time.time()
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
            search_query = f"@mac:{{{mac}}}@vlan:{{{vlan}}}"
            result = await search_redis(redis, search_query, auth_type="MAC")
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
                search_query = f"@ip_addr:{{{ip.replace('.', '\\.')}}}@vlan:{{{vlan}}}"
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
