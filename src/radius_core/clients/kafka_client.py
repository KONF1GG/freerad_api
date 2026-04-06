"""
Модуль для работы с Kafka.

Содержит класс KafkaClient для работы с Kafka
"""

import asyncio
import json
import logging
import ssl
from typing import Optional

from aiokafka import AIOKafkaProducer

from ..config import (
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_CLIENT_ID,
    KAFKA_ACKS,
    KAFKA_LINGER_MS,
    KAFKA_COMPRESSION_TYPE,
    KAFKA_REQUEST_TIMEOUT_MS,
    KAFKA_MAX_REQUEST_SIZE,
    KAFKA_SECURITY_PROTOCOL,
    KAFKA_SASL_MECHANISM,
    KAFKA_SASL_USERNAME,
    KAFKA_SASL_PASSWORD,
    KAFKA_SSL_CAFILE,
    KAFKA_SSL_CERTFILE,
    KAFKA_SSL_KEYFILE,
    KAFKA_SSL_CHECK_HOSTNAME,
)
from ..models import SessionData
from ..core.metrics import track_function

logger = logging.getLogger(__name__)


class KafkaClient:
    """Клиент Kafka с ленивой инициализацией и переиспользованием producer."""

    def __init__(self):
        self._producer: Optional[AIOKafkaProducer] = None
        self._lock = asyncio.Lock()
        self._connection_established = False

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        """Создает SSL context для Kafka, если используется SSL транспорт."""
        if "SSL" not in KAFKA_SECURITY_PROTOCOL.upper():
            return None

        ssl_context = ssl.create_default_context(cafile=KAFKA_SSL_CAFILE or None)
        ssl_context.check_hostname = KAFKA_SSL_CHECK_HOSTNAME

        if KAFKA_SSL_CERTFILE and KAFKA_SSL_KEYFILE:
            ssl_context.load_cert_chain(
                certfile=KAFKA_SSL_CERTFILE,
                keyfile=KAFKA_SSL_KEYFILE,
            )

        return ssl_context

    def _validate_security_config(self) -> None:
        """Проверяет обязательные security-параметры для SASL."""
        protocol = KAFKA_SECURITY_PROTOCOL.upper()
        if "SASL" in protocol and (not KAFKA_SASL_USERNAME or not KAFKA_SASL_PASSWORD):
            raise RuntimeError(
                "Kafka SASL username/password are required for SASL security protocol"
            )

    async def _create_producer(self) -> AIOKafkaProducer:
        self._validate_security_config()
        return AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            client_id=KAFKA_CLIENT_ID,
            acks=KAFKA_ACKS,
            linger_ms=KAFKA_LINGER_MS,
            compression_type=KAFKA_COMPRESSION_TYPE,
            request_timeout_ms=KAFKA_REQUEST_TIMEOUT_MS,
            max_request_size=KAFKA_MAX_REQUEST_SIZE,
            security_protocol=KAFKA_SECURITY_PROTOCOL,
            sasl_mechanism=KAFKA_SASL_MECHANISM,
            sasl_plain_username=KAFKA_SASL_USERNAME or None,
            sasl_plain_password=KAFKA_SASL_PASSWORD or None,
            ssl_context=self._build_ssl_context(),
            value_serializer=lambda value: json.dumps(
                value, ensure_ascii=False, default=str
            ).encode("utf-8"),
        )

    async def get_producer(self) -> AIOKafkaProducer:
        """Получить готовый producer, не пересоздавая его на каждый вызов."""
        if not KAFKA_BOOTSTRAP_SERVERS:
            raise RuntimeError("Kafka bootstrap servers are not configured")

        if not self._connection_established or self._producer is None:
            async with self._lock:
                if not self._connection_established or self._producer is None:
                    self._producer = await self._create_producer()
                    try:
                        await self._producer.start()
                        self._connection_established = True
                        logger.info("Соединение с Kafka успешно установлено")
                    except Exception:
                        self._connection_established = False
                        self._producer = None
                        logger.error("Не удалось установить соединение с Kafka")
                        raise

        return self._producer

    async def send_message(self, topic: str, message: dict) -> bool:
        """Отправить сообщение в Kafka topic."""
        try:
            producer = await self.get_producer()
            await producer.send_and_wait(topic, message)
            return True
        except Exception as e:
            logger.error(
                "Не удалось отправить сообщение в Kafka topic %s: %s", topic, e
            )
            # Позволяем переподключиться при следующей отправке
            self._connection_established = False
            return False

    async def close(self):
        """Закрыть Kafka producer."""
        try:
            if self._producer is not None:
                await self._producer.stop()
        except Exception as e:
            logger.error("Ошибка при закрытии Kafka producer: %s", e)
        finally:
            self._producer = None
            self._connection_established = False

    async def health_check(self) -> bool:
        """Проверка здоровья соединения с Kafka."""
        if not KAFKA_BOOTSTRAP_SERVERS:
            return True

        try:
            producer = await self.get_producer()
            return bool(self._connection_established and producer is not None)
        except Exception as e:
            logger.error("Проверка здоровья Kafka не прошла: %s", e)
            return False


# Глобальный экземпляр клиента
_kafka_client = KafkaClient()


async def get_kafka_client() -> KafkaClient:
    """Получить Kafka клиент."""
    return _kafka_client


@track_function("kafka", "send_message", topic="accounting")
async def kafka_send_accounting(topic: str, session: SessionData) -> bool:
    """Отправить accounting данные в Kafka."""
    payload = session.model_dump(by_alias=True)
    client = await get_kafka_client()
    return await client.send_message(topic, payload)


@track_function("kafka", "send_message", topic="manual")
async def kafka_send_message(topic: str, payload: dict) -> bool:
    """Отправить произвольное сообщение в Kafka (для тестовых вызовов API)."""
    client = await get_kafka_client()
    return await client.send_message(topic, payload)


async def close_kafka():
    """Закрыть Kafka соединение."""
    await _kafka_client.close()


async def kafka_health_check() -> bool:
    """Проверка здоровья Kafka."""
    return await _kafka_client.health_check()
