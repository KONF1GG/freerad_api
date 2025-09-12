"""Операции поиска данных в Redis."""

import json
import logging
import re
from typing import Optional, List, Any, Union
from ...models import SessionData, LoginSearchResult
from ...models.schemas import VideoLoginSearchResult
from ...clients import execute_redis_command

from ...utils import (
    is_username_mac,
    mac_from_username,
    mac_from_hex,
    nasportid_parse,
    username_from_mac,
)
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
) -> Optional[Union[LoginSearchResult, VideoLoginSearchResult]]:
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
            try:
                result = await execute_redis_command(redis, "FT.SEARCH", index, query)
                # Логируем ключ для FT.SEARCH результатов
                if result and result[0] > 0 and len(result) > 1:
                    doc_key = result[1] if len(result) > 1 else "unknown"
                    logger.debug("FT.SEARCH found key: %s", doc_key)
                    # Сохраняем ключ для использования в модели
                    redis_key = doc_key
            except Exception as e:
                if "WRONGTYPE" in str(e):
                    logger.error(
                        "WRONGTYPE error on index %s with query: %s\n"
                        "Индекс может быть поврежден или содержать неправильные типы данных\n"
                        "Полная ошибка: %s",
                        index,
                        query,
                        e,
                    )
                    return None
                else:
                    logger.error(
                        "Ошибка при выполнении FT.SEARCH на индексе %s с запросом %s: %s",
                        index,
                        query,
                        e,
                    )
                    raise
            if not result or result[0] == 0:
                return None
            doc_data = result[2][1]
            if isinstance(doc_data, bytes):
                doc_data = doc_data.decode("utf-8")
            parsed_data = json.loads(doc_data)
        elif key_type == "GET":
            if not redis_key:
                logger.error("redis_key is required for GET operation")
                return None
            try:
                result = await redis.json().get(redis_key)
            except Exception as e:
                logger.error(
                    "Ошибка при выполнении JSON.GET для ключа %s: %s", redis_key, e
                )
                raise
            if not result:
                return None
            parsed_data = result
        else:
            logger.error("Unsupported key_type: %s", key_type)
            return None

        if auth_type:
            parsed_data["auth_type"] = auth_type

        # Добавляем ключ Redis и login для видеокамер
        if auth_type == "VIDEO":
            if redis_key:
                parsed_data["key"] = redis_key

        # Создаем модель в зависимости от типа
        if auth_type == "VIDEO":
            login_result = VideoLoginSearchResult(**parsed_data)
        else:
            login_result = LoginSearchResult(**parsed_data)

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
    redis,
) -> Union[LoginSearchResult, VideoLoginSearchResult]:
    """
    Args:
        session: Данные сессии (AccountingData).
        redis: Redis client
    """

    try:
        # Получаем все данные необходимые для поиска
        nas_port_id = session.NAS_Port_Id
        username = session.User_Name
        is_mac_username = is_username_mac(username)
        remote_id = session.ADSL_Agent_Remote_Id

        # Инициализируем переменные
        mac = ""
        vlan = ""
        onu_mac = ""

        # Извлекаем VLAN
        if nas_port_id:
            nasportid = nasportid_parse(nas_port_id)
            vlan = nasportid.get("cvlan") or nasportid.get("svlan", "")
            vlan = vlan.replace("-", "\\-").replace(":", "\\:")

        if username and is_mac_username:
            mac = mac_from_username(username).replace(":", r"\:")

        if remote_id:
            onu_mac = mac_from_hex(remote_id).replace(":", r"\:")

        # Проверяем обязательные поля
        if not nas_port_id or not vlan or not username:
            logger.warning(
                "Missing required fields: nas_port_id=%s, vlan=%s, username=%s",
                nas_port_id,
                vlan,
                username,
            )
            return LoginSearchResult(mac=mac, vlan=vlan, onu_mac=onu_mac)

        if is_mac_username:
            # Поиск логина по mac+vlan
            search_query = f"@mac:{{{mac}}}@vlan:{{{vlan}}}"
            result = await search_redis(redis, search_query, auth_type="MAC")
            if result:
                return result

            # Поиск камеры по MAC
            search_query = f"@mac:{{{mac}}}"
            result = await search_redis(
                redis, search_query, auth_type="VIDEO", index="idx:device"
            )
            if result:
                return result

            # Поиск логина по onu_mac
            if onu_mac:
                search_query = f"@onu_mac:{{{onu_mac}}}"
                result = await search_redis(redis, search_query, auth_type="OPT82")
                if result:
                    return result

        else:
            static_match = re.match(r"^static-(.+)", username)
            if static_match:
                ip = static_match.groups()[0]
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

        return LoginSearchResult(mac=mac, vlan=vlan, onu_mac=onu_mac)

    except Exception as e:
        logger.error("Critical error in find_login_by_session: %s", e)
        # Возвращаем пустую модель вместо None
        return LoginSearchResult(mac="", vlan="", onu_mac="")


async def find_sessions_by_login(
    login: str, redis, login_data: Optional[LoginSearchResult]
) -> List[SessionData]:
    """Найти и вернуть массив всех сессий по логину."""

    # Строим запрос поиска
    query_parts = []

    # 1. Поиск по логину
    # Экранируем специальные символы в login для RedisSearch
    escaped_login = login.replace("-", r"\-").replace(":", r"\:").replace(".", r"\.")
    if login:
        query_parts.append(f"(@login:{{{escaped_login}}})")

    # Если переданы данные логина, добавляем дополнительные критерии поиска
    if login_data:
        # 2. Поиск по onu_mac
        if hasattr(login_data, "onu_mac") and login_data.onu_mac:
            escaped_onu_mac = login_data.onu_mac.replace(":", r"\:")
            query_parts.append(f"(@onu_mac:{{{escaped_onu_mac}}})")

        # 3. Поиск по mac+vlan (User_Name + NAS_Port)
        if (
            hasattr(login_data, "mac")
            and login_data.mac
            and hasattr(login_data, "vlan")
            and login_data.vlan
        ):
            # Преобразуем MAC в формат User-Name (xx:xx:xx:xx:xx:xx -> xxxx.xxxx.xxxx)
            user_name_format = username_from_mac(login_data.mac)
            if user_name_format:
                # Правильное экранирование точек для RedisSearch
                escaped_user_name = user_name_format.replace(".", r"\.")
                query_parts.append(
                    f"(@User_Name:{{{escaped_user_name}}} @NAS_Port:{{{login_data.vlan}}})"
                )

    # Объединяем все части запроса через OR
    if len(query_parts) == 1:
        query = query_parts[0]
    else:
        query = " | ".join(query_parts)

    # Исключаем VIDEO сессии
    query = f"({query} -@auth_type:{{VIDEO}})"
    index = "idx:radius:session"

    logger.info("Executing FT.SEARCH query: %s on index: %s", query, index)

    try:
        result = await execute_redis_command(
            redis, "FT.SEARCH", index, query, "LIMIT", 0, 10000
        )

        if not result or result[0] == 0:
            logger.debug("No sessions found for login: %s", login)
            return []

        sessions = []
        num_results = result[0]
        logger.debug("Found %d sessions for login: %s", num_results, login)

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


@track_function("redis", "get_camera_login")
async def get_camera_login_from_redis(
    video_login: VideoLoginSearchResult, redis
) -> Optional[str]:
    """
    Получает логин камеры из Redis по ключу camera:%id%.

    Args:
        video_login: Объект VideoLoginSearchResult с данными видеокамеры
        redis: Асинхронный Redis-клиент

    Returns:
        Optional[str]: Логин камеры или None, если не найден
    """
    try:
        if not video_login.key or not video_login.key.startswith("device:cam"):
            logger.warning(
                "Неожиданный формат ключа для видеокамеры: %s", video_login.key
            )
            return None

        # Извлекаем ID камеры из ключа device:cam0001529 -> 1529
        camera_id = video_login.key.replace("device:cam", "").lstrip("0")
        if not camera_id:  # Проверяем, что ID не пустой
            logger.warning("Не удалось извлечь ID камеры из ключа %s", video_login.key)
            return None

        # Получаем данные камеры из ключа camera:%id%
        camera_key = f"camera:{camera_id}"
        camera_data = await execute_redis_command(redis, "JSON.GET", camera_key)

        if not camera_data or not isinstance(camera_data, dict):
            logger.warning("Данные камеры не найдены для ключа %s", camera_key)
            return None

        # Извлекаем login из logins[0]
        logins = camera_data.get("logins", [])
        if not logins or len(logins) == 0:
            logger.warning("Не найдены logins в данных камеры %s", camera_key)
            return None

        camera_login = logins[0]
        logger.info("Получен login для видеокамеры %s: %s", camera_id, camera_login)
        return camera_login

    except Exception as e:
        logger.error(
            "Ошибка при получении данных камеры %s: %s",
            video_login.key if video_login else "unknown",
            e,
        )
        return None
