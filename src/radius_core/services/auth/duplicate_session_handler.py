"""Обработка дублирующих сессий RADIUS."""

import asyncio
import json
import logging
from typing import List, Optional

from radius_core.services.coa.coa_operations import send_coa_session_kill

from ...clients import execute_redis_command
from ...models import SessionData
from ...utils import is_mac_username, mac_from_username

logger = logging.getLogger(__name__)


async def find_duplicate_sessions_by_username_vlan(
    username: str, vlan: str, current_session_id: str, redis
) -> List[SessionData]:
    """Найти дублирующие сессии по username + VLAN"""
    duplicates = []
    try:
        if is_mac_username(username):
            # Поиск по MAC + VLAN для MAC-адресов
            mac = mac_from_username(username).replace(":", r"\:")
            query = f"@mac:{{{mac}}}@vlan:{{{vlan}}}"
        else:
            # Поиск по username + VLAN для обычных логинов
            escaped_username = username.replace("-", r"\-")
            query = f"@username:{{{escaped_username}}}@vlan:{{{vlan}}}"

        result = await execute_redis_command(
            redis, "FT.SEARCH", "idx:radius:session", query
        )

        if result and result[0] > 0:
            num_results = result[0]
            for i in range(num_results):
                fields = result[2 + i * 2]
                if isinstance(fields, list) and len(fields) >= 2 and fields[0] == "$":
                    doc = fields[1]
                    if isinstance(doc, bytes):
                        doc = doc.decode("utf-8")
                    try:
                        session_dict = json.loads(doc)
                        found_session = SessionData(**session_dict)
                        found_uid = getattr(
                            found_session, "Acct_Unique_Session_Id", None
                        )

                        # Исключаем текущую сессию
                        if (
                            found_uid
                            and str(found_uid).strip()
                            != str(current_session_id).strip()
                        ):
                            duplicates.append(found_session)
                    except Exception as e:
                        logger.warning("Не удалось разобрать документ сессии: %s", e)
    except Exception as e:
        logger.error("Ошибка при поиске дублей по username+VLAN: %s", e, exc_info=True)

    return duplicates


async def find_duplicate_sessions_by_onu_mac(
    onu_mac: str, current_session_id: str, redis
) -> List[SessionData]:
    """Найти дублирующие сессии по ONU MAC"""
    duplicates = []
    try:
        query = f"@onu_mac:{{{onu_mac}}}"
        result = await execute_redis_command(
            redis, "FT.SEARCH", "idx:radius:session", query
        )

        if result and result[0] > 0:
            num_results = result[0]
            for i in range(num_results):
                fields = result[2 + i * 2]
                if isinstance(fields, list) and len(fields) >= 2 and fields[0] == "$":
                    doc_data = fields[1]
                    if isinstance(doc_data, bytes):
                        doc_data = doc_data.decode("utf-8")
                    try:
                        session_dict = json.loads(doc_data)
                        found_session = SessionData(**session_dict)
                        found_uid = getattr(
                            found_session, "Acct_Unique_Session_Id", None
                        )

                        # Исключаем текущую сессию
                        if (
                            found_uid
                            and str(found_uid).strip()
                            != str(current_session_id).strip()
                        ):
                            duplicates.append(found_session)
                    except Exception as e:
                        logger.warning("Не удалось разобрать документ сессии: %s", e)
    except Exception as e:
        logger.error("Ошибка при поиске дублей по onu_mac: %s", e, exc_info=True)

    return duplicates


async def find_device_sessions_by_device_data(
    device_ip: Optional[str], device_mac: Optional[str], device_id: str, redis
) -> List[SessionData]:
    """Найти сессии устройства, где IP или MAC не совпадают с ожидаемыми"""
    duplicates = []
    try:
        # Поиск всех активных сессий устройства по device_id или IP
        queries = []

        # Поиск по IP устройства
        if device_ip:
            escaped_ip = device_ip.replace(".", r"\.")
            queries.append(f"@Framed_IP_Address:{{{escaped_ip}}}")

        # Поиск по ID устройства в username
        if device_id:
            queries.append(f"@User_Name:{{{device_id}}}")

        # Поиск по MAC устройства
        if device_mac:
            escaped_mac = device_mac.replace(":", r"\:")
            queries.append(f"@Calling_Station_Id:{{{escaped_mac}}}")

        for query in queries:
            result = await execute_redis_command(
                redis, "FT.SEARCH", "idx:radius:session", query
            )

            if result and result[0] > 0:
                num_results = result[0]
                for i in range(num_results):
                    fields = result[2 + i * 2]
                    if (
                        isinstance(fields, list)
                        and len(fields) >= 2
                        and fields[0] == "$"
                    ):
                        doc_data = fields[1]
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        try:
                            session_dict = json.loads(doc_data)
                            found_session = SessionData(**session_dict)
                            session_ip = getattr(
                                found_session, "Framed_IP_Address", None
                            )
                            session_mac = getattr(
                                found_session, "Calling_Station_Id", None
                            )
                            session_username = getattr(found_session, "User_Name", None)

                            # Проверяем, что данные не совпадают с ожидаемыми
                            ip_mismatch = (
                                device_ip and session_ip and session_ip != device_ip
                            )
                            mac_mismatch = (
                                device_mac and session_mac and session_mac != device_mac
                            )
                            username_mismatch = (
                                device_id
                                and session_username
                                and session_username != device_id
                            )

                            # Если есть несоответствие, считаем это дублирующей сессией
                            if ip_mismatch or mac_mismatch or username_mismatch:
                                duplicates.append(found_session)
                                # logger.debug(
                                #     "Найдена конфликтующая сессия: ID=%s, "
                                #     "IP=%s(ожид.%s), MAC=%s(ожид.%s), "
                                #     "User=%s(ожид.%s)",
                                #     found_session.Acct_Unique_Session_Id,
                                #     session_ip,
                                #     device_ip,
                                #     session_mac,
                                #     device_mac,
                                #     session_username,
                                #     device_id,
                                # )
                        except Exception as e:
                            logger.warning(
                                "Не удалось разобрать документ сессии устройства: %s",
                                e,
                            )
    except Exception as e:
        logger.error("Ошибка при поиске сессий устройства: %s", e, exc_info=True)

    return duplicates


async def kill_duplicate_sessions(
    sessions: List[SessionData], reason: str, rabbitmq=None
) -> None:
    """Завершить список дублирующих сессий"""

    tasks = []
    for session in sessions:
        session_id = getattr(session, "Acct_Unique_Session_Id", None)
        if session_id:
            logger.info("Завершаем дублирующую сессию: %s (%s)", session_id, reason)
            tasks.append(send_coa_session_kill(session, rabbitmq))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
