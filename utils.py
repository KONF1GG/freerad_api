from datetime import datetime, timezone
from dateutil import parser
import re
import logging
from typing import Dict, Any, Tuple, Union
from datetime import timedelta

logger = logging.getLogger(__name__)

EVENT_FMT = "%b %d %Y %H:%M:%S +05"


def parse_event(ts: str | datetime | dict) -> datetime:
    """Парсит время в любом виде и возвращает datetime в UTC"""
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
    return dt_utc


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


PREFIX_MAP: Dict[str, Tuple[int, str, str, bool]] = {
    "0x454c5458": (10, "ELTX{}", "", False),
    "0x485754436a": (10, "70:A5:{}", ":", False),
    "0x48575443a6": (10, "80:F7:{}", ":", False),
    "0x48575443e6": (10, "E0:E8:{}", ":", False),
    "0x485754431d": (10, "50:5B:{}", ":", False),
    "0x485754433641": (10, "70:A5:{}", ":", True),
    "0x485754434136": (10, "80:F7:{}", ":", True),
    "0x485754433144": (10, "50:5B:{}", ":", True),
    "0x34383537353434333641": (18, "70:A5:{}", ":", True),
    "0x34383537353434334136": (18, "80:F7:{}", ":", True),
    "0x34383537353434333144": (18, "50:5B:{}", ":", True),
}


def mac_from_hex(hex_var: str) -> str:
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


def repl_none(data: Union[Dict, Any]) -> Union[Dict, Any]:
    """Замена None значений на пустые строки"""
    if isinstance(data, dict):
        return {k: repl_none(v) for k, v in data.items()}
    elif data is None:
        return ""
    else:
        return data
