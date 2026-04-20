"""
Модуль для работы с Kafka.

Содержит класс KafkaClient для работы с Kafka
"""

import asyncio
import json
import logging
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

    def _resolve_security_protocol(self) -> str:
        """Нормализует protocol к режиму без TLS/сертификатов."""
        protocol = KAFKA_SECURITY_PROTOCOL.upper()

        if protocol == "SSL":
            logger.warning("Kafka protocol SSL is disabled, using PLAINTEXT")
            return "PLAINTEXT"

        if protocol == "SASL_SSL":
            logger.warning("Kafka protocol SASL_SSL is disabled, using SASL_PLAINTEXT")
            return "SASL_PLAINTEXT"

        if protocol not in {"PLAINTEXT", "SASL_PLAINTEXT"}:
            logger.warning(
                "Unknown Kafka protocol %s, using PLAINTEXT",
                protocol,
            )
            return "PLAINTEXT"

        return protocol

    def _validate_security_config(self, protocol: str) -> None:
        """Проверяет обязательные security-параметры для SASL."""
        if "SASL" in protocol and (not KAFKA_SASL_USERNAME or not KAFKA_SASL_PASSWORD):
            raise RuntimeError(
                "Kafka SASL username/password are required for SASL security protocol"
            )

    async def _create_producer(self) -> AIOKafkaProducer:
        security_protocol = self._resolve_security_protocol()
        self._validate_security_config(security_protocol)

        config = {
            "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
            "client_id": KAFKA_CLIENT_ID,
            "acks": KAFKA_ACKS,
            "linger_ms": KAFKA_LINGER_MS,
            "compression_type": KAFKA_COMPRESSION_TYPE,
            "request_timeout_ms": KAFKA_REQUEST_TIMEOUT_MS,
            "max_request_size": KAFKA_MAX_REQUEST_SIZE,
            "security_protocol": security_protocol,
            "value_serializer": lambda value: json.dumps(
                value, ensure_ascii=False, default=str
            ).encode("utf-8"),
        }

        if "SASL" in security_protocol:
            config.update(
                {
                    "sasl_mechanism": KAFKA_SASL_MECHANISM,
                    "sasl_plain_username": KAFKA_SASL_USERNAME or None,
                    "sasl_plain_password": KAFKA_SASL_PASSWORD or None,
                }
            )

        return AIOKafkaProducer(**config)

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
                        try:
                            await self._producer.stop()
                        except Exception:
                            pass
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
