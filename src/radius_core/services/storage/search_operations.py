"""Операции поиска данных в Redis."""

import json
import logging
import re
from typing import Optional, List, Any
from ...models import SessionData, LoginSearchResult
from ...clients import execute_redis_command, get_redis
from ...utils import is_mac_username, mac_from_username, mac_from_hex, nasportid_parse
from ...config import RADIUS_LOGIN_PREFIX
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


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
                logger.debug("No results for %s query: %s", key_type, query)
                return None
            doc_data = result[2][1]
            if isinstance(doc_data, bytes):
                doc_data = doc_data.decode("utf-8")
            parsed_data = json.loads(doc_data)
        elif key_type == "GET":
            if not redis_key:
                logger.error("redis_key is required for GET operation")
                return None
            result = await execute_redis_command(redis, "GET", redis_key)
            if not result:
                logger.debug("No results for %s key: %s", key_type, redis_key)
                return None
            if isinstance(result, bytes):
                result = result.decode("utf-8")
            parsed_data = json.loads(result)
        else:
            logger.error("Unsupported key_type: %s", key_type)
            return None

        if auth_type:
            parsed_data["auth_type"] = auth_type
        # Создаем модель LoginSearchResult
        login_result = LoginSearchResult(**parsed_data)
        logger.debug("Search result: %s", login_result)
        return login_result

    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON from search result: %s", e)
        return None
    except Exception as e:
        logger.error("Search operation failed: %s", e)
        return None


@track_function("redis", "find_login")
async def find_login_by_session(
    session: Any,
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
            logger.warning("Could not extract VLAN from NAS-Port-Id: %s", nas_port_id)
            return None

        username = session.User_Name
        if not username:
            logger.warning("Missing User-Name in session")
            return None

        logger.debug("Searching login: username=%s, VLAN=%s", username, vlan)

        if is_mac_username(username):
            logger.debug("IPoE session, MAC username: %s", username)
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
            logger.debug("PPPoE/static session, username: %s", username)
            static_match = re.match(r"^static-(.+)", username)
            if static_match:
                ip = static_match.groups()[0]
                logger.debug("Static session, IP: %s", ip)
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

        logger.info("Login not found: username=%s, VLAN=%s", username, vlan)
        return None

    except Exception as e:
        logger.error("Critical error in find_login_by_session: %s", e)
        return None


async def find_sessions_by_login(login: str, redis) -> List[SessionData]:
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
                        logger.warning("Failed to parse session data: %s", e)

        return sessions

    except Exception as e:
        logger.error("Error searching sessions for login '%s': %s", login, e)
        return []
