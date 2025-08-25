"""Операции для отправки данных в RabbitMQ очереди."""

import logging
from datetime import datetime, timezone
from typing import Optional

from ...models import SessionData, TrafficData, AuthDataLog
from ...clients import rmq_send_message
from ...config import (
    AMQP_SESSION_QUEUE,
    AMQP_TRAFFIC_QUEUE,
    AMQP_AUTH_LOG_QUEUE,
)

logger = logging.getLogger("radius_core")


async def send_to_session_queue(
    session_data: SessionData, stoptime: bool = False
) -> bool:
    """Отправка данных сессии в RabbitMQ очередь для сохранения"""
    logger.info("Отправка сессии в очередь: %s", session_data.Acct_Unique_Session_Id)

    try:
        # Убеждаемся что все времена в UTC формате
        if session_data.Event_Timestamp:
            # Приводим к UTC если нужно
            if session_data.Event_Timestamp.tzinfo is None:
                session_data.Event_Timestamp = session_data.Event_Timestamp.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Event_Timestamp = session_data.Event_Timestamp.astimezone(
                    timezone.utc
                )

        # Устанавливаем Acct_Update_Time если он еще не установлен
        if not session_data.Acct_Update_Time:
            session_data.Acct_Update_Time = (
                session_data.Event_Timestamp or datetime.now(tz=timezone.utc)
            )

        # Приводим Acct_Update_Time к UTC
        if session_data.Acct_Update_Time.tzinfo is None:
            session_data.Acct_Update_Time = session_data.Acct_Update_Time.replace(
                tzinfo=timezone.utc
            )
        else:
            session_data.Acct_Update_Time = session_data.Acct_Update_Time.astimezone(
                timezone.utc
            )

        # Приводим Acct_Start_Time к UTC если он есть
        if session_data.Acct_Start_Time:
            if session_data.Acct_Start_Time.tzinfo is None:
                session_data.Acct_Start_Time = session_data.Acct_Start_Time.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Acct_Start_Time = session_data.Acct_Start_Time.astimezone(
                    timezone.utc
                )

        # Обрабатываем Acct_Stop_Time
        if stoptime:
            if not session_data.Acct_Stop_Time:
                session_data.Acct_Stop_Time = (
                    session_data.Event_Timestamp or datetime.now(tz=timezone.utc)
                )
            # Приводим к UTC
            if session_data.Acct_Stop_Time.tzinfo is None:
                session_data.Acct_Stop_Time = session_data.Acct_Stop_Time.replace(
                    tzinfo=timezone.utc
                )
            else:
                session_data.Acct_Stop_Time = session_data.Acct_Stop_Time.astimezone(
                    timezone.utc
                )
            logger.debug(
                "Сессия будет остановлена: %s", session_data.Acct_Unique_Session_Id
            )
        else:
            # Активная сессия, не заполняем Stop-Time
            logger.debug("Активная сессия, Acct-Stop-Time будет пустым")
            session_data.Acct_Stop_Time = None

        result = await rmq_send_message(AMQP_SESSION_QUEUE, session_data)
        if result:
            logger.info(
                "Сессия отправлена в очередь session_queue: %s",
                session_data.Acct_Unique_Session_Id,
            )
        return result
    except Exception as e:
        logger.error(
            "Ошибка при сохранении сессии %s: %s",
            session_data.Acct_Unique_Session_Id,
            e,
        )
        raise


async def send_to_traffic_queue(
    session_new: SessionData, session_stored: Optional[SessionData] = None
) -> bool:
    """Отправка данных трафика в RabbitMQ очередь для сохранения"""

    try:
        # Определяем поля трафика и их алиасы
        traffic_fields = [
            ("Acct_Input_Octets", "Acct-Input-Octets"),
            ("Acct_Output_Octets", "Acct-Output-Octets"),
            ("Acct_Input_Packets", "Acct-Input-Packets"),
            ("Acct_Output_Packets", "Acct-Output-Packets"),
            ("ERX_IPv6_Acct_Input_Octets", "ERX-IPv6-Acct-Input-Octets"),
            ("ERX_IPv6_Acct_Output_Octets", "ERX-IPv6-Acct-Output-Octets"),
            ("ERX_IPv6_Acct_Input_Packets", "ERX-IPv6-Acct-Input-Packets"),
            ("ERX_IPv6_Acct_Output_Packets", "ERX-IPv6-Acct-Output-Packets"),
        ]

        # Базовые данные с алиасами
        traffic_data = {
            "Acct-Session-Id": session_new.Acct_Session_Id,
            "Acct-Unique-Session-Id": session_new.Acct_Unique_Session_Id,
            "Event-Timestamp": session_new.Event_Timestamp,
            "Acct-Update-Time": session_new.Acct_Update_Time,
        }

        # Добавляем поля трафика с алиасами
        for field, alias in traffic_fields:
            value = getattr(session_new, field, 0)
            if value is not None:
                traffic_data[alias] = value

        # Если есть старая сессия, вычисляем дельту
        if session_stored:
            logger.debug(
                "Вычисление дельты трафика для %s", session_new.Acct_Unique_Session_Id
            )
            negative_deltas = []

            for field, alias in traffic_fields:
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


async def save_auth_log_to_queue(auth_data: AuthDataLog) -> bool:
    """Отправка лога авторизации в RabbitMQ очередь"""
    logger.debug("Сохранение лога авторизации: %s", auth_data.username)
    try:
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
