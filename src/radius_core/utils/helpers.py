"""
Модуль для вспомогательных функций.
"""

import re
import logging
from typing import Dict, Tuple
from datetime import datetime, timezone
from dateutil import parser

logger = logging.getLogger(__name__)


def parse_event(ts: str | datetime | dict) -> datetime:
    """Парсит время в любом виде и возвращает datetime в UTC"""
    dt = None
    try:
        if isinstance(ts, dict):
            ts = ts.get("value", [0])[0]
            dt = parser.parse(ts)  # type: ignore[arg-type]
            dt = datetime.now(timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, str):
            dt = parser.parse(ts)
        else:
            logger.error("Unsupported type for event timestamp: %s", type(ts))
            dt = datetime.now(timezone.utc)
    except (ValueError, TypeError, OSError) as e:
        logger.error("Failed to parse event timestamp '%s': %s", ts, e)
        dt = datetime.now(timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc


def now_str() -> str:
    """Возвращает текущую дату и время в формате YYYY-MM-DD HH:MM:SS"""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def nasportid_parse(nasportid: str) -> Dict[str, str]:
    """Парсит nasportid в словарь"""
    m = re.match(
        r"^(?P<psiface>ps\d+)\.\d+\:(?P<svlan>\d+)\-?(?P<cvlan>\d+)?$",
        nasportid,
    )
    return m.groupdict() if m else {"psiface": "", "svlan": "", "cvlan": ""}


def is_mac_username(username: str) -> bool:
    """Проверяет, является ли username MAC-адресом"""
    return bool(re.match(r"^([0-9a-f]{4}\.){2}([0-9a-f]{4})$", username))


def mac_from_username(username: str) -> str:
    """Извлечение MAC-адреса из username"""
    if not username:
        return ""
    mac = (
        username[0:2]
        + ":"
        + username[2:4]
        + ":"
        + username[5:7]
        + ":"
        + username[7:9]
        + ":"
        + username[10:12]
        + ":"
        + username[12:14]
    ).upper()
    return mac


def username_from_mac(mac: str) -> str:
    """Преобразование MAC-адреса в формат User-Name (e848.b850.98fb)"""
    if not mac:
        return ""

    # Убираем двоеточия и приводим к нижнему регистру
    mac_clean = mac.replace(":", "").replace("-", "").replace(".", "").lower()

    # Проверяем, что MAC-адрес содержит 12 символов
    if len(mac_clean) != 12:
        return ""

    # Преобразуем в формат xxxx.xxxx.xxxx
    user_name_format = f"{mac_clean[0:4]}.{mac_clean[4:8]}.{mac_clean[8:12]}"
    return user_name_format


PREFIX_MAP: Dict[str, Tuple[int, str, str, bool]] = {
    "0x454c5458": (10, "ELTX{}", "", False),
    "0x485754436a": (10, "70:A5:{}", ":", False),
    "0x48575443a6": (10, "80:F7:{}", ":", False),
    "0x48575443e6": (10, "E0:E8:{}", ":", False),
    "0x485754431d": (10, "50:5B:{}", ":", False),
    "0x485754433641": (10, "70:A5:{}", ":", True),
    "0x485754434136": (10, "80:F7:{}", ":", True),
    "0x485754433144": (10, "50:5B:{}", ":", True),
    "0x485754434146": (10, "D0:5F:{}", ":", True),
    "0x34383537353434333641": (18, "70:A5:{}", ":", True),
    "0x34383537353434334136": (18, "80:F7:{}", ":", True),
    "0x34383537353434333144": (18, "50:5B:{}", ":", True),
    "0x34383537353434334146": (18, "D0:5F:{}", ":", True),
}


def mac_from_hex(hex_var: str) -> str:
    """Преобразует hex в MAC-адрес"""
    hex_var = hex_var.lower()
    for prefix, (offset, template, sep, need_decode) in PREFIX_MAP.items():
        if hex_var.startswith(prefix):
            body = hex_var[offset:]
            if need_decode:
                body = bytearray.fromhex(body).decode()
            if sep:
                parts = [body[i : i + 2] for i in range(0, len(body), 2)]
                return template.format(sep.join(parts)).upper()
            else:
                return template.format(body).upper()
    return ":".join(hex_var[i : i + 2] for i in range(2, 14, 2)).upper()
