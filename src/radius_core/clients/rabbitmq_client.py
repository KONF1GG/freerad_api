"""
Модуль для работы с RabbitMQ.

Содержит класс RabbitMQClient для работы с RabbitMQ,
а также функции для отправки сообщений и проверки здоровья соединения.
"""

import asyncio
import json
import logging
from typing import Optional

import aio_pika
from aio_pika import DeliveryMode, ExchangeType
from ..config import AMQP_URL, AMQP_EXCHANGE, AMQP_SESSION_QUEUE, AMQP_TRAFFIC_QUEUE
from ..models import RABBIT_MODELS
from ..core.metrics import track_function

logger = logging.getLogger(__name__)


class RabbitMQClient:
    """Класс для работы с RabbitMQ"""

    def __init__(self):
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractChannel] = None
        self._exchange: Optional[aio_pika.abc.AbstractExchange] = None
        self._lock = asyncio.Lock()

    async def get_channel(self) -> aio_pika.abc.AbstractChannel:
        """Получить канал RabbitMQ с автоматической настройкой"""
        if self._channel is None or self._channel.is_closed:
            async with self._lock:
                if self._channel is None or self._channel.is_closed:
                    try:
                        self._connection = await aio_pika.connect_robust(
                            AMQP_URL,
                            heartbeat=30,
                            blocked_connection_timeout=300,
                        )
                        self._channel = await self._connection.channel()

                        await self._channel.set_qos(prefetch_count=100)

                        self._exchange = await self._channel.declare_exchange(
                            name=AMQP_EXCHANGE,
                            type=ExchangeType.DIRECT,
                            durable=True,
                        )

                        # Создаем очереди
                        # await self._setup_queues()

                        logger.info("Соединение с RabbitMQ успешно установлено")
                    except Exception:
                        logger.error("Не удалось установить соединение с RabbitMQ")
                        raise
        return self._channel

    async def _setup_queues(self):
        """Настройка очередей"""
        if not self._channel or not self._exchange:
            return

        # Очередь сессий
        session_queue = await self._channel.declare_queue(
            name=AMQP_SESSION_QUEUE,
            durable=True,
            arguments={
                "x-message-ttl": 3600000,  # 1 час
                "x-max-length": 10000,
            },
        )
        await session_queue.bind(self._exchange, routing_key=AMQP_SESSION_QUEUE)

        # Очередь трафика
        traffic_queue = await self._channel.declare_queue(
            name=AMQP_TRAFFIC_QUEUE,
            durable=True,
            arguments={
                "x-message-ttl": 1800000,  # 30 минут
                "x-max-length": 50000,
            },
        )
        await traffic_queue.bind(self._exchange, routing_key=AMQP_TRAFFIC_QUEUE)

    async def send_message(
        self, routing_key: str, message: RABBIT_MODELS, persistent: bool = True
    ) -> bool:
        """Отправить сообщение в RabbitMQ"""
        try:
            if not self._exchange:
                logger.error("Exchange not initialized")
                return False

            # Подготавливаем сообщение с алиасами (дефисами)
            body = json.dumps(
                message.model_dump(by_alias=True), ensure_ascii=False, default=str
            ).encode("utf-8")

            message_obj = aio_pika.Message(
                body=body,
                delivery_mode=DeliveryMode.PERSISTENT
                if persistent
                else DeliveryMode.NOT_PERSISTENT,
                content_type="application/json",
                content_encoding="utf-8",
                timestamp=None,
            )

            # Отправляем сообщение
            await self._exchange.publish(
                message_obj,
                routing_key=routing_key,
            )

            logger.debug("Сообщение отправлено в %s: %s байт", routing_key, len(body))
            return True

        except (AttributeError, ConnectionError, TimeoutError) as e:
            logger.error("Не удалось отправить сообщение в %s: %s", routing_key, e)
            return False

    async def close(self):
        """Закрыть соединение с RabbitMQ"""
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except (ConnectionError, TimeoutError, AttributeError) as e:
            logger.error("Ошибка при закрытии соединения с RabbitMQ: %s", e)
        finally:
            self._channel = None
            self._connection = None
            self._exchange = None

    async def health_check(self) -> bool:
        """Проверка здоровья соединения"""
        try:
            await self.get_channel()
            return self._channel is not None and not self._channel.is_closed
        except (ConnectionError, TimeoutError, AttributeError) as e:
            logger.error("Проверка здоровья соединения с RabbitMQ не прошла: %s", e)
            return False


# Глобальный экземпляр клиента
_rabbitmq_client = RabbitMQClient()


async def get_rabbitmq_client() -> RabbitMQClient:
    """Получить RabbitMQ клиент"""
    return _rabbitmq_client


@track_function("rabbitmq", "send_message")
async def rmq_send_message(routing_key: str, message: RABBIT_MODELS) -> bool:
    """Отправить сообщение в RabbitMQ"""

    try:
        client = await get_rabbitmq_client()
        result = await client.send_message(routing_key, message)
        return result
    except Exception:
        raise


async def close_rabbitmq():
    """Закрыть RabbitMQ соединение"""
    await _rabbitmq_client.close()


async def rabbitmq_health_check() -> bool:
    """Проверка здоровья RabbitMQ"""
    return await _rabbitmq_client.health_check()
