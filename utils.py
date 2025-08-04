from datetime import datetime, timezone
from dateutil import parser
import re
import logging
from typing import Dict, Any, Tuple, Union
from datetime import timedelta

logger = logging.getLogger(__name__)

EVENT_FMT = "%b %d %Y %H:%M:%S +05"


def parse_event(ts: str | datetime | dict) -> str:
    """Парсит время в любом виде и возвращает строку в UTC: 'YYYY-MM-DD HH:MM:SS'"""
    dt = None
    try:
        if isinstance(ts, dict):
            ts = ts.get("value", [0])[0]
            dt = parser.parse(ts)  # type: ignore[arg-type]
            if ts is None:
                dt = datetime.now(timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, str):
            dt = parser.parse(ts)
        else:
            logger.error(f"Unsupported type for event timestamp: {type(ts)}")
            dt = datetime.now(timezone.utc)
    except Exception as e:
        logger.error(f"Failed to parse event timestamp '{ts}': {e}")
        dt = datetime.now(timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S")


def now_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


PREFIX_MAP: Dict[str, Tuple[int, str, str]] = {
    "0x454c5458": (10, "ELTX{}", ""),
    "0x485754436a": (10, "70:A5:{}", ":"),
    "0x48575443a6": (10, "80:F7:{}", ":"),
    "0x48575443e6": (10, "E0:E8:{}", ":"),
    "0x485754431d": (10, "50:5B:{}", ":"),
    "0x485754433641": (10, "70:A5:{}", ":"),
    "0x485754434136": (10, "80:F7:{}", ":"),
    "0x485754433144": (10, "50:5B:{}", ":"),
    "0x34383537353434333641": (18, "70:A5:{}", ":"),
    "0x34383537353434334136": (18, "80:F7:{}", ":"),
    "0x34383537353434333144": (18, "50:5B:{}", ":"),
}


def mac_from_hex(hex_var: str) -> str:
    hex_var = hex_var.lower()
    for prefix, (offset, template, sep) in PREFIX_MAP.items():
        if hex_var.startswith(prefix):
            body = hex_var[offset:]
            if sep:
                parts = [body[i : i + 2] for i in range(0, len(body), 2)]
                return template.format(sep.join(parts)).upper()
            else:
                return template.format(body).upper()
    return ":".join(hex_var[i : i + 2] for i in range(2, 14, 2)).upper()


def repl_none(data: Union[Dict, Any]) -> Union[Dict, Any]:
    """Замена None значений на пустые строки"""
    if isinstance(data, dict):
        return {k: repl_none(v) for k, v in data.items()}
    elif data is None:
        return ""
    else:
        return data
