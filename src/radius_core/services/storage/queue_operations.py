"""Операции для отправки данных в RabbitMQ очереди."""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from radius_core.utils.helpers import now_str

from ...models import SessionData, TrafficData, AuthDataLog
from ...clients import rmq_send_message
from ...config import (
    AMQP_SESSION_QUEUE,
    AMQP_TRAFFIC_QUEUE,
    AMQP_AUTH_LOG_QUEUE,
)

logger = logging.getLogger(__name__)

# Кэш для UTC timezone
_UTC_TZ = timezone.utc

# Константа для полей трафика
TRAFFIC_FIELDS = [
    ("Acct_Input_Octets", "Acct-Input-Octets"),
    ("Acct_Output_Octets", "Acct-Output-Octets"),
    ("Acct_Input_Packets", "Acct-Input-Packets"),
    ("Acct_Output_Packets", "Acct-Output-Packets"),
    ("ERX_IPv6_Acct_Input_Octets", "ERX-IPv6-Acct-Input-Octets"),
    ("ERX_IPv6_Acct_Output_Octets", "ERX-IPv6-Acct-Output-Octets"),
    ("ERX_IPv6_Acct_Input_Packets", "ERX-IPv6-Acct-Input-Packets"),
    ("ERX_IPv6_Acct_Output_Packets", "ERX-IPv6-Acct-Output-Packets"),
]


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Быстрое приведение времени к UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC_TZ)
    if dt.tzinfo == _UTC_TZ:
        return dt
    return dt.astimezone(_UTC_TZ)


async def send_to_session_queue(
    session_data: SessionData, stoptime: bool = False
) -> bool:
    """Отправка данных сессии в RabbitMQ очередь для сохранения"""
    logger.info("Отправка сессии в очередь: %s", session_data.Acct_Unique_Session_Id)

    try:
        # Создаем копию для работы, чтобы не мутировать исходный объект
        session_copy = session_data.model_copy()
        
        # Быстрая оптимизация временных меток
        if session_copy.Event_Timestamp:
            session_copy.Event_Timestamp = _ensure_utc(session_copy.Event_Timestamp)

        # Устанавливаем Acct_Update_Time если он еще не установлен
        if not session_copy.Acct_Update_Time:
            session_copy.Acct_Update_Time = (
                session_copy.Event_Timestamp or datetime.now(tz=_UTC_TZ)
            )
        else:
            session_copy.Acct_Update_Time = _ensure_utc(session_copy.Acct_Update_Time)

        # Приводим Acct_Start_Time к UTC если он есть
        if session_copy.Acct_Start_Time:
            session_copy.Acct_Start_Time = _ensure_utc(session_copy.Acct_Start_Time)

        # Обрабатываем Acct_Stop_Time
        if stoptime:
            if not session_copy.Acct_Stop_Time:
                session_copy.Acct_Stop_Time = (
                    session_copy.Event_Timestamp or datetime.now(tz=_UTC_TZ)
                )
            session_copy.Acct_Stop_Time = _ensure_utc(session_copy.Acct_Stop_Time)
        else:
            # Активная сессия, не заполняем Stop-Time
            session_copy.Acct_Stop_Time = None

        result = await rmq_send_message(AMQP_SESSION_QUEUE, session_copy)
        if result:
            logger.info(
                "Сессия отправлена в очередь session_queue: %s",
                session_copy.Acct_Unique_Session_Id,
            )
        return result
    except Exception as e:
        logger.error(
            "Ошибка при сохранении сессии %s: %s",
            session_data.Acct_Unique_Session_Id,
            e,
        )
        return False


async def send_to_traffic_queue(
    session_new: SessionData, session_stored: Optional[SessionData] = None
) -> bool:
    """Отправка данных трафика в RabbitMQ очередь для сохранения"""

    try:
        # Валидация входных данных
        if not session_new:
            logger.error("session_new не может быть None")
            return False
        
        if not session_new.Acct_Unique_Session_Id:
            logger.error("Acct_Unique_Session_Id обязателен")
            return False

        # Базовые данные с алиасами
        traffic_data: Dict[str, Any] = {
            "Acct-Unique-Session-Id": session_new.Acct_Unique_Session_Id,
            "login": session_new.login,
            "timestamp": now_str(),
        }

        # Если есть старая сессия, вычисляем дельту
        if session_stored:
            negative_deltas = []

            for field, alias in TRAFFIC_FIELDS:
                new_value = getattr(session_new, field, 0) or 0
                old_value = getattr(session_stored, field, 0) or 0
                delta = new_value - old_value

                if delta < 0:
                    negative_deltas.append(f"{alias}: {delta}")
                    logger.warning(
                        "Отрицательная дельта для %s: %s -> %s = %s",
                        alias,
                        old_value,
                        new_value,
                        delta,
                    )
                    delta = 0  # Не позволяем отрицательным значениям

                traffic_data[alias] = delta

            if negative_deltas:
                logger.warning(
                    "Отрицательная дельта трафика для %s: %s",
                    session_new.Acct_Unique_Session_Id,
                    "; ".join(negative_deltas),
                )
        else:
            # Добавляем поля трафика с алиасами для новой сессии
            for field, alias in TRAFFIC_FIELDS:
                value = getattr(session_new, field, 0)
                if value is not None:
                    traffic_data[alias] = value

        traffic_model = TrafficData(**traffic_data)

        # Отправляем в RabbitMQ
        result = await rmq_send_message(AMQP_TRAFFIC_QUEUE, traffic_model)

        if result:
            action = "дельта" if session_stored else "полный"
            logger.info(
                "Трафик (%s) отправлен в очередь: %s",
                action,
                session_new.Acct_Unique_Session_Id,
            )

        return result

    except Exception as e:
        logger.error(
            "Ошибка сохранения трафика для %s: %s",
            session_new.Acct_Unique_Session_Id,
            e,
        )
        return False


async def send_auth_log_to_queue(auth_data: AuthDataLog) -> bool:
    """Отправка лога авторизации в RabbitMQ очередь"""
    try:
        # Валидация входных данных
        if not auth_data:
            logger.error("auth_data не может быть None")
            return False
            
        if not auth_data.username:
            logger.error("username обязателен для лога авторизации")
            return False
            
        result = await rmq_send_message(AMQP_AUTH_LOG_QUEUE, auth_data)
        if result:
            logger.info("Лог авторизации отправлен в очередь: %s", auth_data.username)
        return result
    except Exception as e:
        logger.error(
            "Ошибка сохранения лога авторизации для %s: %s",
            auth_data.username,
            e,
        )
        return False