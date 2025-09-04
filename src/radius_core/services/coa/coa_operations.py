"""Операции CoA (Change of Authorization) для RADIUS сессий."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import aio_pika

from ...config import AMQP_COA_QUEUE, AMQP_COA_EXCHANGE
from ...models import SessionData
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("rabbitmq", "send_coa")
async def send_coa_to_queue(
    request_type: str,
    session_data: Dict[str, Any],
    channel,
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
        coa_exchange = await channel.declare_exchange(
            AMQP_COA_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )

        # Объявляем очередь для CoA запросов (пассивно, чтобы не изменять существующие параметры)
        queue = await channel.declare_queue(AMQP_COA_QUEUE, durable=True, passive=True)

        # Привязываем очередь к exchange с правильным routing key
        await queue.bind(coa_exchange, routing_key="coa.request.*")

        logger.debug(
            "Очередь %s привязана к exchange %s с routing key coa.request.*",
            AMQP_COA_QUEUE,
            AMQP_COA_EXCHANGE,
        )

        # Формируем сообщение
        message_data = _build_coa_message(
            request_type, session_data, attributes, reason
        )

        # Определяем routing key в зависимости от типа запроса
        routing_key = f"coa.request.{request_type}"

        # Отправляем сообщение через COA exchange
        message_body = json.dumps(message_data, ensure_ascii=False).encode()
        message = aio_pika.Message(
            body=message_body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )

        # Отправляем сообщение с подтверждением
        result = await coa_exchange.publish(message, routing_key=routing_key)

        # Проверяем, было ли сообщение доставлено
        if not result:
            logger.error(
                "Сообщение не было доставлено в exchange %s с routing key %s",
                AMQP_COA_EXCHANGE,
                routing_key,
            )
            return False

        logger.info(
            "CoA запрос %s отправлен в очередь через exchange %s с routing key %s, "
            "размер сообщения: %d байт, данные: %s",
            request_type,
            AMQP_COA_EXCHANGE,
            routing_key,
            len(message_body),
            message_data,
        )
        return True

    except Exception as e:
        logger.error("Ошибка отправки CoA запроса в очередь: %s", e, exc_info=True)
        return False


async def check_coa_queue_status(channel) -> Dict[str, Any]:
    """
    Проверка состояния очереди CoA запросов

    Returns:
        Dict с информацией о состоянии очереди
    """
    try:
        # Объявляем exchange и очередь (пассивно, чтобы не изменять существующие параметры)
        coa_exchange = await channel.declare_exchange(
            AMQP_COA_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True, passive=True
        )
        queue = await channel.declare_queue(AMQP_COA_QUEUE, durable=True, passive=True)

        # Получаем информацию о очереди
        queue_info = queue.declaration_result

        status = {
            "queue_name": AMQP_COA_QUEUE,
            "exchange_name": AMQP_COA_EXCHANGE,
            "message_count": queue_info.message_count,
            "consumer_count": queue_info.consumer_count,
            "status": "ok",
        }

        logger.info("Статус очереди CoA: %s", status)
        return status

    except Exception as e:
        logger.error("Ошибка проверки состояния очереди CoA: %s", e, exc_info=True)
        return {
            "queue_name": AMQP_COA_QUEUE,
            "exchange_name": AMQP_COA_EXCHANGE,
            "message_count": 0,
            "consumer_count": 0,
            "status": "error",
            "error": str(e),
        }


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
    session_req: SessionData, channel, reason: Optional[str] = None
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
            "login": session_req.login,
            "Acct-Session-Id": session_req.Acct_Session_Id,
            "NAS-IP-Address": session_req.NAS_IP_Address,
        }
        # logger.debug("Данные сессии для отправки Coa kill: %s", session_data)

        # Отправляем CoA kill запрос в очередь
        success = await send_coa_to_queue("kill", data_for_coa, channel, reason=reason)

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
    channel,
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
            "login": session_req.login,
            "Acct-Session-Id": session_req.Acct_Session_Id,
            "NAS-IP-Address": session_req.NAS_IP_Address,
        }
        # logger.debug("Данные сессии для отправки Coa update: %s", session_data)

        # Отправляем CoA update запрос в очередь
        success = await send_coa_to_queue(
            "update",
            data_for_coa,
            channel,
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
