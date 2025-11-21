"""
Модуль для вспомогательных функций.
"""

import re
import logging
from typing import Dict, Optional, Tuple
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
        r"^(?P<psiface>\w+\d+)\.\d*\:?(?P<svlan>\d+)(?:\-(?P<cvlan>\d+))?$",
        nasportid,
    )
    return m.groupdict() if m else {"psiface": "", "svlan": "", "cvlan": ""}


def is_username_mac(username: str) -> bool:
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
    "0x48575443af": (10, "D0:5F:{}", ":", False),
    "0x485754433641": (10, "70:A5:{}", ":", True),
    "0x485754434136": (10, "80:F7:{}", ":", True),
    "0x485754434536": (10, "E0:E8:{}", ":", True),
    "0x485754433144": (10, "50:5B:{}", ":", True),
    "0x485754434146": (10, "D0:5F:{}", ":", True),
    "0x34383537353434333641": (18, "70:A5:{}", ":", True),
    "0x34383537353434334136": (18, "80:F7:{}", ":", True),
    "0x34383537353434334536": (18, "E0:E8:{}", ":", True),
    "0x34383537353434333144": (18, "50:5B:{}", ":", True),
    "0x34383537353434334146": (18, "D0:5F:{}", ":", True),
    "0x3435344335343538": (18, "ELTX{}", "", True),
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


def parse_service_name(service_session: str) -> Optional[str]:
    """Парсит название услуги из ERX_Service_Session без скобок.

    Примеры:
    - INET-FREEDOM(100k) -> INET-FREEDOM
    - INET-SOCIAL(323k) -> INET-SOCIAL
    - INET-CHILDREN(2332k) -> INET-CHILDREN
    - NOINET-NOMONEY -> NOINET-NOMONEY
    """
    try:
        if not service_session:
            return None

        match = re.match(r"^([A-Z0-9-]+)", service_session)
        if match:
            return match.group(1)
        return None
    except (ValueError, AttributeError) as e:
        logger.warning("Ошибка парсинга названия услуги из %s: %s", service_session, e)
        return None


def mac_to_ipv6_ula(mac_addr: str) -> Optional[Tuple[str, str]]:
    """Преобразует MAC-адрес в IPv6 ULA адреса.

    Преобразует MAC-адрес в два IPv6 ULA адреса:
    - framed-ipv6-prefix: "fd00::xxxx:xxxx:xxxx:1/128"
    - delegated-ipv6-prefix: "fd00:deed:xxxx:xxxx::/64"

    Args:
        mac_addr: MAC-адрес в любом формате ("d4bf.7f52.e8dc", "04:bf:6d:64:ee:07" и т.д.)

    Returns:
        Кортеж (framed-ipv6-prefix, delegated-ipv6-prefix) или None если MAC невалидный

    Пример:
        >>> mac_to_ipv6_ula("d4bf.7f52.e8dc")
        ('fd00::d4bf:7f52:e8dc:1/128', 'fd00:deed:7f52:e8dc::/64')
    """
    if not mac_addr:
        return None

    # Очищаем MAC адрес от разделителей
    mac_clean = mac_addr.replace(":", "").replace("-", "").replace(".", "").lower()

    # Проверяем, что это валидное 16-ричное значение длиной 12 символов
    if not re.match(r"^[0-9a-f]{12}$", mac_clean):
        return None

    # Разбиваем на сегменты по 4 символа
    segments = [mac_clean[i : i + 4] for i in range(0, 12, 4)]

    # framed-ipv6-prefix: используем все сегменты MAC
    framed = f"fd00::{':'.join(segments)}:1/128"

    # delegated-ipv6-prefix: заменяем первый сегмент на "deed", используем последние 2 сегмента
    delegated = f"fd00:deed:{segments[1]}:{segments[2]}::/64"

    return (framed, delegated)