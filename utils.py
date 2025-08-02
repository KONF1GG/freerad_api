import time
import re
import json
import logging
from typing import Dict, Any, Union
from redis_client import get_redis
from config import RADIUS_LOGIN_PREFIX

logger = logging.getLogger(__name__)

EVENT_FMT = "%b %d %Y %H:%M:%S +05"


def parse_event(ts: str) -> int:
    """Парсинг временной метки события"""
    try:
        return int(time.mktime(time.strptime(ts, EVENT_FMT)))
    except ValueError as e:
        logger.error(f"Failed to parse event timestamp '{ts}': {e}")
        return int(time.time())


def nasportid_parse(nasportid: str) -> Dict[str, str]:
    m = re.match(
        r"^(?P<psiface>ps\d+)\.\d+\:(?P<svlan>\d+)\-?(?P<cvlan>\d+)?$",
        nasportid,
    )
    return m.groupdict() if m else {"psiface": "", "svlan": "", "cvlan": ""}


def is_mac_username(username: str) -> bool:
    return bool(re.match(r"^([0-9a-f]{4}\.){2}([0-9a-f]{4})$", username))


def mac_from_username(username: str) -> str:
    """Извлечение MAC-адреса из username"""
    if not username:
        return ""
    # Приводим к стандартному формату xx:xx:xx:xx:xx:xx
    mac = username.replace("-", ":").lower()
    return mac


def mac_from_hex(hex_string: str) -> str:
    """Конвертация HEX строки в MAC-адрес"""
    try:
        # Убираем лишние символы и приводим к нижнему регистру
        clean_hex = re.sub(r"[^0-9a-fA-F]", "", hex_string).lower()
        if len(clean_hex) == 12:
            # Разбиваем на пары символов
            mac_parts = [clean_hex[i : i + 2] for i in range(0, 12, 2)]
            return ":".join(mac_parts)
    except Exception as e:
        logger.error(f"Error converting hex '{hex_string}' to MAC: {e}")
    return ""


def repl_none(data: Union[Dict, Any]) -> Union[Dict, Any]:
    """Замена None значений на пустые строки"""
    if isinstance(data, dict):
        return {k: repl_none(v) for k, v in data.items()}
    elif data is None:
        return ""
    else:
        return data


def process_traffic_data(session_req):
    """Преобразование данных трафика для сессии."""
    if session_req["Acct_Status_Type"] == "Start":
        return session_req
    session_req["Acct_Input_Octets"] = (
        int(session_req.get("Acct_Input_Gigawords", 0)) << 32
    ) | int(session_req.get("Acct_Input_Octets", 0))
    session_req["Acct_Output_Octets"] = (
        int(session_req.get("Acct_Output_Gigawords", 0)) << 32
    ) | int(session_req.get("Acct_Output_Octets", 0))
    session_req["Acct_Input_Packets"] = (
        int(session_req.get("ERX_Input_Gigapkts", 0)) << 32
    ) | int(session_req.get("Acct_Input_Packets", 0))
    session_req["Acct_Output_Packets"] = (
        int(session_req.get("ERX_Output_Gigapkts", 0)) << 32
    ) | int(session_req.get("Acct_Output_Packets", 0))
    session_req["ERX_IPv6_Acct_Input_Octets"] = (
        int(session_req.get("ERX_IPv6_Acct_Input_Gigawords", 0)) << 32
    ) | int(session_req.get("ERX_IPv6_Acct_Input_Octets", 0))
    session_req["ERX_IPv6_Acct_Output_Octets"] = (
        int(session_req.get("ERX_IPv6_Acct_Output_Gigawords", 0)) << 32
    ) | int(session_req.get("ERX_IPv6_Acct_Output_Octets", 0))
    session_req["ERX_IPv6_Acct_Input_Packets"] = int(
        session_req.get("ERX_IPv6_Acct_Input_Packets", 0)
    )
    session_req["ERX_IPv6_Acct_Output_Packets"] = int(
        session_req.get("ERX_IPv6_Acct_Output_Packets", 0)
    )
    session_req["ERX_IPv6_Acct_Input_Gigawords"] = int(
        session_req.get("ERX_IPv6_Acct_Input_Gigawords", 0)
    )
    session_req["ERX_IPv6_Acct_Output_Gigawords"] = int(
        session_req.get("ERX_IPv6_Acct_Output_Gigawords", 0)
    )
    return session_req


async def find_login_by_session(session: Dict[str, Any]) -> Union[Dict[str, Any], bool]:
    """Асинхронный поиск логина по данным сессии"""
    start_time = time.time()

    try:
        redis = await get_redis()

        nas_port_id = session.get("NAS_Port_Id")
        if not nas_port_id:
            logger.warning("Missing NAS-Port-Id in session")
            return False

        nasportid = nasportid_parse(nas_port_id)
        vlan = nasportid.get("cvlan") or nasportid.get("svlan", "")
        if not vlan:
            logger.warning(f"Could not extract VLAN from NAS-Port-Id: {nas_port_id}")
            return False

        username = session.get("User_Name", "")
        if not username:
            logger.warning("Missing User-Name in session")
            return False

        logger.debug(f"Searching login: username={username}, VLAN={vlan}")

        if is_mac_username(username):
            logger.debug(f"IPoE session, MAC username: {username}")

            try:
                search_query = (
                    "@mac:{"
                    + mac_from_username(username).replace(":", r"\:")
                    + "}@vlan:{"
                    + vlan
                    + "}"
                )
                logins = await redis.execute_command(
                    "FT.SEARCH", "idx:radius:login", search_query
                )

                if (
                    logins and len(logins) > 2 and logins[1] == 1
                ):  # logins[0] - total count, logins[1] - number of results
                    doc_data = logins[3]  # logins[2] - doc id, logins[3] - doc data
                    if isinstance(doc_data, bytes):
                        doc_data = doc_data.decode("utf-8")
                    ret = json.loads(doc_data)
                    ret["auth_type"] = "MAC"
                    logger.debug(
                        f"Найден логин по MAC+VLAN: {ret.get('login', 'unknown')}",
                    )
                    return repl_none(ret)

            except Exception as e:
                logger.warning(f"Error searching by MAC+VLAN: {e}")

            # Поиск по ONU MAC (если есть ADSL-Agent-Remote-Id)
            remote_id = session.get("ADSL_Agent_Remote_Id")
            if remote_id:
                try:
                    onu_mac = mac_from_hex(remote_id)
                    search_query = "@onu_mac:{" + onu_mac.replace(":", r"\:") + "}"
                    logins = await redis.execute_command(
                        "FT.SEARCH", "idx:radius:login", search_query
                    )

                    if (
                        logins and len(logins) > 2 and logins[1] == 1
                    ):  # logins[0] - total count, logins[1] - number of results
                        doc_data = logins[3]  # logins[2] - doc id, logins[3] - doc data
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        ret = json.loads(doc_data)
                        ret["auth_type"] = "OPT82"
                        logger.debug(
                            f"Найден логин по ONU MAC: {ret.get('login', 'unknown')}",
                        )
                        return repl_none(ret)

                except Exception as e:
                    logger.warning(f"Error searching by ONU MAC: {e}")

        else:
            # PPPoE или статическая аутентификация
            logger.debug(f"PPPoE/static session, username: {username}")

            # Статическая сессия
            static_match = re.match(r"^static-(.+)", username)
            if static_match:
                ip = static_match.groups()[0]
                logger.debug(f"Static session, IP: {ip}")

                try:
                    search_query = (
                        "@ip_addr:{" + ip.replace(".", "\\.") + "}@vlan:{" + vlan + "}"
                    )
                    logins = await redis.execute_command(
                        "FT.SEARCH", "idx:radius:login", search_query
                    )

                    if (
                        logins and len(logins) > 2 and logins[1] == 1
                    ):  # logins[0] - total count, logins[1] - number of results
                        doc_data = logins[3]  # logins[2] - doc id, logins[3] - doc data
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        ret = json.loads(doc_data)
                        ret["auth_type"] = "STATIC"
                        logger.debug(
                            f"Найден логин для статической сессии: {ret.get('login', 'unknown')}",
                        )
                        return repl_none(ret)

                except Exception as e:
                    logger.warning(f"Error searching static login: {e}")
            else:
                # PPPoE логин
                try:
                    login_key = f"{RADIUS_LOGIN_PREFIX}{username.strip().lower()}"
                    logger.debug(f"Searching PPPoE login, key: {login_key}")

                    login_json = await redis.get(login_key)

                    if login_json:
                        login_data = (
                            json.loads(login_json)
                            if isinstance(login_json, str)
                            else login_json
                        )
                        if isinstance(login_data, dict):
                            login_data["auth_type"] = "PPPOE"
                            logger.info(
                                f"Found PPPoE login: {login_data.get('login', 'unknown')}"
                            )
                            return repl_none(login_data)

                except Exception as e:
                    logger.warning(f"Error searching PPPoE login: {e}")

        logger.info(f"Login not found: username={username}, VLAN={vlan}")
        return False

    except Exception as e:
        logger.error(f"Critical error in find_login_by_session: {e}")
        return False
    finally:
        exec_time = time.time() - start_time
        logger.debug(f"Login search took {exec_time:.3f}s")


def enrich_session_with_login(
    session_req: Dict[str, Any], login: Dict[str, Any]
) -> Dict[str, Any]:
    """Добавление данных логина в сессию"""
    if login and isinstance(login, dict):
        session_req["login"] = login.get("login", "")
        session_req["auth_type"] = login.get("auth_type", "UNKNOWN")
        session_req["contract"] = login.get("contract", "")
        session_req["onu_mac"] = login.get("onu_mac", "")
    else:
        session_req["auth_type"] = "UNAUTH"
        session_req["login"] = ""
        session_req["contract"] = ""
        session_req["onu_mac"] = ""

    if session_req.get("ERX_Service_Session"):
        session_req["service"] = session_req["ERX_Service_Session"]

    return session_req
