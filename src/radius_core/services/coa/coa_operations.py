"""Операции CoA (Change of Authorization) для RADIUS сессий."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import aio_pika

from ...config import AMQP_COA_QUEUE
from ...models import SessionData
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("rabbitmq", "send_coa")
async def send_coa_to_queue(
    request_type: str,
    session_data: Dict[str, Any],
    rabbitmq,
    attributes: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> bool:
    """
    Отправка CoA запроса в очередь RabbitMQ

    Args:
        request_type: Тип запроса ("kill" или "update")
        session_data: Данные сессии
        attributes: Атрибуты для изменения (только для update)

    Returns:
        bool: True если запрос успешно отправлен, False в противном случае
    """
    try:
        # Объявляем очередь для CoA запросов
        queue = await rabbitmq.declare_queue(AMQP_COA_QUEUE, durable=True)

        # Создаем копию session_data с преобразованными datetime объектами
        processed_session_data = _process_session_data_for_coa(session_data)

        # Формируем сообщение
        message_data = _build_coa_message(
            request_type, processed_session_data, attributes, reason
        )

        # Отправляем сообщение в очередь
        await rabbitmq.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message_data, ensure_ascii=False).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=queue.name,
        )

        # logger.debug(
        #     "CoA запрос %s отправлен в очередь для сессии %s",
        #     request_type,
        #     processed_session_data.get("Acct-Session-Id", "unknown"),
        # )
        return True

    except Exception as e:
        logger.error("Ошибка отправки CoA запроса в очередь: %s", e, exc_info=True)
        return False


def _process_session_data_for_coa(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразует datetime объекты в строки"""
    if not isinstance(session_data, dict):
        logger.error(
            "session_data должен быть словарем, получен: %s", type(session_data)
        )
        return {}

    processed_session_data = {}
    for key, value in session_data.items():
        if isinstance(value, datetime):
            processed_session_data[key] = value.isoformat()
        else:
            processed_session_data[key] = value
    return processed_session_data


def _build_coa_message(
    request_type: str,
    session_data_for_coa: Dict[str, Any],
    attributes: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Строит сообщение для CoA запроса"""
    message_data = {
        "type": request_type,
        "reason": reason,
        "session_data": session_data_for_coa,
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Добавляем атрибуты для update запросов
    if request_type == "update" and attributes:
        message_data["attributes"] = attributes

    return message_data


async def send_coa_session_kill(
    session_req: SessionData, rabbitmq, reason: Optional[str] = None
) -> bool:
    """
    Отправка команды на завершение сессии через CoA (в очередь)

    Args:
        session_req: Данные сессии для завершения
        rabbitmq: Клиент RabbitMQ

    Returns:
        bool: True если команда успешно отправлена
    """
    try:
        data_for_coa = {
            "Acct-Session-Id": session_req.Acct_Session_Id,
            "NAS-IP-Address": session_req.NAS_IP_Address,
            "NAS-Port-Id": session_req.NAS_Port_Id,
        }
        # logger.debug("Данные сессии для отправки Coa kill: %s", session_data)

        # Отправляем CoA kill запрос в очередь
        success = await send_coa_to_queue("kill", data_for_coa, rabbitmq, reason=reason)

        if success:
            logger.info(
                "CoA команда на завершение сессии отправлена в очередь: %s",
                data_for_coa.get("Acct-Session-Id", "unknown"),
            )
        else:
            logger.warning(
                "CoA команда на завершение сессии не была отправлена в очередь: %s",
                data_for_coa.get("Acct-Session-Id", "unknown"),
            )

        return success

    except Exception as e:
        logger.error(
            "Ошибка отправки CoA команды на завершение сессии в очередь: %s",
            e,
            exc_info=True,
        )
        return False


async def send_coa_session_set(
    session_req: SessionData,
    rabbitmq,
    attributes: Dict[str, Any],
    reason: Optional[str] = None,
) -> bool:
    """
    Отправка команды на обновление атрибутов сессии через CoA (в очередь)

    Args:
        session_req: Данные сессии для обновления
        rabbitmq: Клиент RabbitMQ
        attributes: Атрибуты для обновления

    Returns:
        bool: True если команда успешно отправлена
    """
    try:
        data_for_coa = {
            "Acct-Session-Id": session_req.Acct_Session_Id,
            "NAS-IP-Address": session_req.NAS_IP_Address,
            "NAS-Port-Id": session_req.NAS_Port_Id,
        }
        # logger.debug("Данные сессии для отправки Coa update: %s", session_data)

        # Отправляем CoA update запрос в очередь
        success = await send_coa_to_queue(
            "update",
            data_for_coa,
            rabbitmq,
            attributes,
            reason=reason,
        )

        if success:
            logger.info(
                "CoA команда на обновление сессии отправлена в очередь: %s, "
                "атрибуты: %s",
                data_for_coa.get("Acct_Session_Id", "unknown"),
                attributes,
            )
        else:
            logger.warning(
                "CoA команда на обновление сессии не была отправлена в очередь: %s, "
                "атрибуты: %s",
                data_for_coa.get("Acct_Session_Id", "unknown"),
                attributes,
            )

        return success

    except Exception as e:
        logger.error(
            "Ошибка отправки CoA команды на обновление сессии в очередь: %s",
            e,
            exc_info=True,
        )
        return False
