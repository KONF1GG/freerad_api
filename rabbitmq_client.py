import aio_pika
import json
import asyncio
import logging
from typing import Optional
from aio_pika import DeliveryMode, ExchangeType
from aio_pika.exceptions import ChannelClosed as AioPikaChannelClosed
from aiormq.exceptions import ChannelClosed as AiormqChannelClosed
from aio_pika.exceptions import AMQPException
from config import (
    AMQP_URL,
    AMQP_EXCHANGE,
    AMQP_SESSION_QUEUE,
    AMQP_TRAFFIC_QUEUE,
    AMQP_CONNECT_TIMEOUT,
)
import metrics
from schemas import RABBIT_MODELS

logger = logging.getLogger(__name__)


class RabbitMQClient:
    def __init__(self):
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractChannel] = None
        self._exchange: Optional[aio_pika.abc.AbstractExchange] = None
        self._lock = asyncio.Lock()

    async def get_channel(self) -> aio_pika.abc.AbstractChannel:
        """Получить активный канал (с автонастройкой/переподключением)."""
        await self.ensure_connected()
        # На этом этапе канал гарантированно инициализирован
        return self._channel  # type: ignore[return-value]

    async def ensure_connected(self) -> None:
        """Гарантировать активное соединение, канал и exchange.

        Потокобезопасно восстанавливает соединение/канал/объекты при сбоях.
        """
        if (
            self._connection is not None
            and not self._connection.is_closed
            and self._channel is not None
            and not self._channel.is_closed
            and self._exchange is not None
        ):
            return

        async with self._lock:
            # Повторная проверка внутри критической секции
            if (
                self._connection is not None
                and not self._connection.is_closed
                and self._channel is not None
                and not self._channel.is_closed
                and self._exchange is not None
            ):
                return

            try:
                # Создаем/восстанавливаем робастное соединение
                if self._connection is None or self._connection.is_closed:
                    self._connection = await asyncio.wait_for(
                        aio_pika.connect_robust(
                            AMQP_URL,
                            heartbeat=30,
                            blocked_connection_timeout=300,
                        ),
                        timeout=AMQP_CONNECT_TIMEOUT,
                    )

                # Создаем/восстанавливаем канал с подтверждениями публикаций
                if self._channel is None or self._channel.is_closed:
                    self._channel = await self._connection.channel(
                        publisher_confirms=True
                    )
                    await self._channel.set_qos(prefetch_count=100)

                # Создаем/восстанавливаем exchange и очереди (идемпотентно)
                self._exchange = await self._channel.declare_exchange(
                    name=AMQP_EXCHANGE,
                    type=ExchangeType.DIRECT,
                    durable=True,
                )

                await self._setup_queues()

                logger.info("RabbitMQ connection/channel are ready")
            except Exception as e:
                # Сбрасываем состояние, чтобы попытаться заново при следующем вызове
                logger.error(f"Failed to (re)connect to RabbitMQ: {e}")
                self._exchange = None
                self._channel = None
                try:
                    if self._connection and not self._connection.is_closed:
                        await self._connection.close()
                finally:
                    self._connection = None
                raise

    async def _setup_queues(self):
        """Настройка очередей"""
        if not self._channel or not self._exchange:
            return

        # Очередь сессий - сначала пытаемся подключиться к существующей
        try:
            session_queue = await self._channel.declare_queue(
                name=AMQP_SESSION_QUEUE,
                durable=True,
                passive=True,  # Только подключиться к существующей
            )
        except Exception:
            # Если очереди нет, создаем новую с аргументами
            session_queue = await self._channel.declare_queue(
                name=AMQP_SESSION_QUEUE,
                durable=True,
                arguments={
                    "x-message-ttl": 3600000,  # 1 час
                    "x-max-length": 10000,
                },
            )
        await session_queue.bind(self._exchange, routing_key=AMQP_SESSION_QUEUE)

        # Очередь трафика - сначала пытаемся подключиться к существующей
        try:
            traffic_queue = await self._channel.declare_queue(
                name=AMQP_TRAFFIC_QUEUE,
                durable=True,
                passive=True,  # Только подключиться к существующей
            )
        except Exception:
            # Если очереди нет, создаем новую с аргументами
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
        start = asyncio.get_event_loop().time()
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

        attempts = 2  # первая попытка + одна переподключение/повтор
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                # Гарантируем активное подключение и объекты
                await self.ensure_connected()
                if not self._exchange:
                    # should not happen, но на всякий случай
                    raise RuntimeError("Exchange is not initialized")

                # Публикация без дополнительного таймаута (используем robust connection)
                await self._exchange.publish(
                    message_obj,
                    routing_key=routing_key,
                )

                duration = asyncio.get_event_loop().time() - start
                metrics.record_external_call("rabbitmq", "publish", "success", duration)
                logger.debug(
                    f"Message sent to {routing_key}: {len(body)} bytes (attempt {attempt})"
                )
                return True

            except (AioPikaChannelClosed, AiormqChannelClosed, AMQPException) as e:
                # Канал/соединение закрыт — пробуем восстановиться один раз
                last_error = e
                logger.warning(
                    f"RabbitMQ channel/connection issue on publish (attempt {attempt}): {e}. Reconnecting..."
                )
                # Сбросим объекты и попробуем переподключиться на следующей итерации
                await self._safe_reset()
                # Небольшая пауза перед повтором
                await asyncio.sleep(0.1)
                continue
            except Exception as e:
                last_error = e
                break

        # Если дошли сюда — публикация не удалась
        duration = asyncio.get_event_loop().time() - start
        metrics.record_external_call("rabbitmq", "publish", "error", duration)
        logger.error(
            f"Failed to send message to {routing_key}: {last_error!r} after {attempts} attempts"
        )
        return False

    async def _safe_reset(self) -> None:
        """Осторожно закрыть и обнулить объекты подключения."""
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
        except Exception:
            pass
        finally:
            self._channel = None
        try:
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except Exception:
            pass
        finally:
            self._connection = None
            self._exchange = None

    async def close(self):
        """Закрыть соединение с RabbitMQ"""
        try:
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {e}")
        finally:
            self._channel = None
            self._connection = None
            self._exchange = None

    async def health_check(self) -> bool:
        """Проверка здоровья соединения"""
        try:
            await self.get_channel()
            return self._channel is not None and not self._channel.is_closed
        except Exception as e:
            logger.error(f"RabbitMQ health check failed: {e}")
            return False


# Глобальный экземпляр клиента
_rabbitmq_client = RabbitMQClient()


async def get_rabbitmq_client() -> RabbitMQClient:
    """Получить RabbitMQ клиент"""
    return _rabbitmq_client


async def rmq_send_message(routing_key: str, message: RABBIT_MODELS) -> bool:
    """Отправить сообщение в RabbitMQ"""
    client = await get_rabbitmq_client()
    return await client.send_message(routing_key, message)


async def close_rabbitmq():
    """Закрыть RabbitMQ соединение"""
    await _rabbitmq_client.close()


async def rabbitmq_health_check() -> bool:
    """Проверка здоровья RabbitMQ"""
    return await _rabbitmq_client.health_check()
