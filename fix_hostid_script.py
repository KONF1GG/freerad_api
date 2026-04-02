#!/usr/bin/env python3
"""
Скрипт для быстрого исправления всех документов device:* со строковыми полями hostId.
Конвертирует строковые значения hostId в числа для совместимости с RediSearch индексом.

Работает в три этапа:
1. Сканирование: анализирует все документы и находит те, у которых hostId строковый
2. Обновление: исправляет найденные документы (конвертирует hostId в число)
3. Пересоздание индекса: удаляет старый индекс и создает новый с hostId как NUMERIC

Запуск:
    # Полный цикл (сканирование + обновление + пересоздание индекса)
    python fix_hostid_script.py

    # Только сканирование (без обновления)
    DRY_RUN=true python fix_hostid_script.py

    # С настройками
    BATCH_SIZE=200 SCAN_RESULTS_FILE=my_results.json python fix_hostid_script.py

Переменные окружения:
    BATCH_SIZE - размер пачки для обработки (по умолчанию: 100)
    SCAN_RESULTS_FILE - файл для сохранения результатов сканирования
    DRY_RUN - только сканирование без обновления (true/false)
    REDIS_URL - URL подключения к Redis
    DEVICE_INDEX_NAME - имя индекса устройств (по умолчанию: idx:device)

Требования:
    - redis.asyncio
    - tqdm
    - Доступ к Redis серверу
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, List, Optional, Tuple

import redis.asyncio as redis
from tqdm import tqdm

# Импортируем конфигурацию из проекта
sys.path.append("src")
from src.radius_core.config.settings import REDIS_URL

# Настройка логирования
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Redis конфигурация из проекта
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_PASSWORD = None  # Укажите пароль если нужен

# Индекс устройств
DEVICE_INDEX_NAME = os.getenv("DEVICE_INDEX_NAME", "idx:device")

# Размер пачки для mget операций (можно переопределить через переменную окружения)
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

# Файл для сохранения результатов сканирования
SCAN_RESULTS_FILE = os.getenv("SCAN_RESULTS_FILE", "hostid_scan_results.json")

# Сухой запуск (только сканирование без обновления)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")


def parse_host_id(value: Any) -> Optional[int]:
    """Пытается безопасно преобразовать значение hostId в int."""
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    return None


async def get_redis_connection():
    """Создает подключение к Redis."""
    try:
        # Используем REDIS_URL из конфигурации если доступен
        if REDIS_URL:
            redis_client = redis.from_url(
                REDIS_URL,
                decode_responses=False,  # Получаем bytes для корректной обработки
                socket_connect_timeout=5,
                socket_timeout=5,
            )
        else:
            # Fallback на старые параметры если REDIS_URL не настроен
            redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                decode_responses=False,  # Получаем bytes для корректной обработки
                socket_connect_timeout=5,
                socket_timeout=5,
            )

        # Проверяем подключение
        await redis_client.ping()
        logger.info("Успешное подключение к Redis")
        return redis_client
    except ImportError:
        logger.error("Не установлен redis.asyncio. Установите: pip install redis")
        sys.exit(1)
    except Exception as e:
        logger.error("Ошибка подключения к Redis: %s", e)
        sys.exit(1)


async def scan_device_keys(redis_client) -> List[str]:
    """Сканирует все ключи device:* в Redis."""
    logger.info("Сканирование ключей device:*")
    device_keys = []
    cursor = 0

    try:
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor, match="device:*", count=1000
            )
            device_keys.extend([key.decode("utf-8") for key in keys])

            if cursor == 0:
                break

        logger.info("Найдено %s ключей device:*", len(device_keys))
        return device_keys
    except Exception as e:
        logger.error("Ошибка при сканировании ключей: %s", e)
        return []


async def scan_hostid_fields(
    redis_client, keys: List[str]
) -> Tuple[List[Tuple[str, dict, str, int]], List[Tuple[str, Any]]]:
    """Находит документы, где hostId строковый и может быть преобразован в число."""
    convertible_docs: List[Tuple[str, dict, str, int]] = []
    invalid_docs: List[Tuple[str, Any]] = []

    try:
        docs = await redis_client.json().mget(keys, "$")

        for key, doc_list in zip(keys, docs):
            if not doc_list or len(doc_list) == 0:
                continue

            doc_data = doc_list[0]
            if "hostId" not in doc_data:
                continue

            current_value = doc_data["hostId"]

            # Нужно исправлять только строковые hostId
            if isinstance(current_value, str):
                parsed_value = parse_host_id(current_value)
                if parsed_value is not None:
                    convertible_docs.append(
                        (key, doc_data, current_value, parsed_value)
                    )
                else:
                    invalid_docs.append((key, current_value))

        return convertible_docs, invalid_docs

    except Exception as e:
        logger.warning("Ошибка при сканировании пачки документов: %s", e)
        return [], []


async def fix_hostid_field_batch(
    redis_client, docs_batch: List[Tuple[str, dict, str, int]]
) -> List[str]:
    """Исправляет поля hostId в пачке документов."""
    fixed_keys = []

    try:
        update_commands = []
        for key, doc_data, old_hostid, new_hostid in docs_batch:
            doc_data["hostId"] = new_hostid
            update_commands.append((key, doc_data))
            fixed_keys.append(key)

            logger.debug(
                "Подготовлено исправление %s: hostId '%s' -> %s",
                key,
                old_hostid,
                new_hostid,
            )

        if update_commands:
            pipeline = redis_client.pipeline()
            for key, doc_data in update_commands:
                pipeline.json().set(key, "$", doc_data)

            await pipeline.execute()
            logger.info("Исправлено %s документов в пачке", len(fixed_keys))

        return fixed_keys

    except Exception as e:
        logger.warning("Ошибка при исправлении пачки документов: %s", e)
        return []


async def recreate_device_index(redis_client, index_name: str = DEVICE_INDEX_NAME):
    """Пересоздает индекс устройств с hostId как NUMERIC."""
    try:
        try:
            await redis_client.execute_command("FT.DROPINDEX", index_name)
            logger.info("Старый индекс %s удален", index_name)
        except Exception as e:
            if "Unknown Index name" in str(e):
                logger.info("Индекс %s не существовал, создаем новый", index_name)
            else:
                logger.warning("Ошибка при удалении индекса %s: %s", index_name, e)

        create_command = [
            "FT.CREATE",
            index_name,
            "ON",
            "JSON",
            "PREFIX",
            "1",
            "device:",
            "SCHEMA",
            "$.mac",
            "AS",
            "mac",
            "TAG",
            "$.host",
            "AS",
            "host",
            "TEXT",
            "$.ipAddress",
            "AS",
            "ip",
            "TAG",
            "$.hostId",
            "AS",
            "hostId",
            "NUMERIC",
        ]

        await redis_client.execute_command(*create_command)
        logger.info("Новый индекс %s создан с hostId как NUMERIC", index_name)

    except Exception as e:
        logger.error("Ошибка при пересоздании индекса %s: %s", index_name, e)
        raise


async def save_scan_results(
    convertible_docs: List[Tuple[str, dict, str, int]],
    invalid_docs: List[Tuple[str, Any]],
    filename: str,
):
    """Сохраняет результаты сканирования в JSON файл."""
    try:
        results = {
            "convertible": [],
            "invalid": [],
            "summary": {
                "convertible_count": len(convertible_docs),
                "invalid_count": len(invalid_docs),
            },
        }

        for key, doc_data, old_hostid, new_hostid in convertible_docs:
            results["convertible"].append(
                {
                    "key": key,
                    "old_hostId": old_hostid,
                    "new_hostId": new_hostid,
                    "doc_size": len(str(doc_data)),
                }
            )

        for key, bad_value in invalid_docs:
            results["invalid"].append(
                {
                    "key": key,
                    "bad_hostId": bad_value,
                }
            )

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        logger.info("Результаты сканирования сохранены в %s", filename)
        logger.info("Конвертируемых hostId: %s", len(convertible_docs))
        logger.info("Некорректных hostId: %s", len(invalid_docs))

    except Exception as e:
        logger.warning("Ошибка при сохранении результатов сканирования: %s", e)


async def main():
    """Основная функция."""
    logger.info(DRY_RUN)
    logger.info("Запуск скрипта исправления полей hostId...")

    if REDIS_URL:
        logger.info("Используется REDIS_URL из конфигурации: %s", REDIS_URL)
    else:
        logger.info("Используется fallback конфигурация: %s:%s", REDIS_HOST, REDIS_PORT)

    logger.info("Используется индекс: %s", DEVICE_INDEX_NAME)
    logger.info("Размер пачки: %s", BATCH_SIZE)

    redis_client = await get_redis_connection()

    try:
        device_keys = await scan_device_keys(redis_client)
        if not device_keys:
            logger.warning("Ключи device:* не найдены")
            return

        total_count = len(device_keys)
        logger.info("Найдено %s ключей device:*. Начинаем сканирование...", total_count)

        convertible_docs: List[Tuple[str, dict, str, int]] = []
        invalid_docs: List[Tuple[str, Any]] = []

        logger.info("Этап 1: Сканирование документов со строковыми полями hostId...")
        with tqdm(
            total=total_count, desc="Сканирование документов", unit="doc"
        ) as pbar:
            for i in range(0, total_count, BATCH_SIZE):
                batch_keys = device_keys[i : i + BATCH_SIZE]

                batch_convertible, batch_invalid = await scan_hostid_fields(
                    redis_client, batch_keys
                )
                convertible_docs.extend(batch_convertible)
                invalid_docs.extend(batch_invalid)

                pbar.update(len(batch_keys))

                if (i + BATCH_SIZE) % (
                    BATCH_SIZE * 10
                ) == 0 or i + BATCH_SIZE >= total_count:
                    logger.info(
                        "Просканировано: %s/%s (%.1f%%), Конвертируемых: %s, Некорректных: %s",
                        min(i + BATCH_SIZE, total_count),
                        total_count,
                        min(i + BATCH_SIZE, total_count) / total_count * 100,
                        len(convertible_docs),
                        len(invalid_docs),
                    )

        logger.info(
            "Сканирование завершено! Конвертируемых hostId: %s, Некорректных hostId: %s",
            len(convertible_docs),
            len(invalid_docs),
        )

        if convertible_docs or invalid_docs:
            await save_scan_results(convertible_docs, invalid_docs, SCAN_RESULTS_FILE)

        if invalid_docs:
            logger.warning(
                "Найдены неконвертируемые hostId. Они НЕ были изменены. Проверьте файл %s",
                SCAN_RESULTS_FILE,
            )

        if not convertible_docs:
            logger.info("Строковые hostId, которые можно конвертировать, не найдены")
            return

        if DRY_RUN:
            logger.info("Режим DRY_RUN: обновление документов пропущено")
            logger.info("Для выполнения обновлений запустите скрипт без DRY_RUN=true")
            return

        logger.info("Этап 2: Обновление документов со строковыми полями hostId...")
        fixed_count = 0

        with tqdm(
            total=len(convertible_docs), desc="Обновление документов", unit="doc"
        ) as pbar:
            for i in range(0, len(convertible_docs), BATCH_SIZE):
                batch_docs = convertible_docs[i : i + BATCH_SIZE]

                fixed_keys = await fix_hostid_field_batch(redis_client, batch_docs)
                fixed_count += len(fixed_keys)

                pbar.update(len(batch_docs))

                if (i + BATCH_SIZE) % (BATCH_SIZE * 10) == 0 or i + BATCH_SIZE >= len(
                    convertible_docs
                ):
                    logger.info(
                        "Обновлено: %s/%s (%.1f%%), Всего исправлено: %s",
                        min(i + BATCH_SIZE, len(convertible_docs)),
                        len(convertible_docs),
                        min(i + BATCH_SIZE, len(convertible_docs))
                        / len(convertible_docs)
                        * 100,
                        fixed_count,
                    )

        logger.info(
            "Обновление завершено! Исправлено %s из %s документов",
            fixed_count,
            len(convertible_docs),
        )

        if fixed_count > 0:
            logger.info("Этап 3: Пересоздание RediSearch индекса устройств...")
            await recreate_device_index(redis_client, DEVICE_INDEX_NAME)
            logger.info("Индекс успешно пересоздан")
            logger.info(
                "Рекомендуется перезапустить приложение для применения изменений"
            )

    except KeyboardInterrupt:
        logger.info("Скрипт прерван пользователем")
    except Exception as e:
        logger.error("Критическая ошибка: %s", e)
    finally:
        await redis_client.close()
        logger.info("Подключение к Redis закрыто")


if __name__ == "__main__":
    asyncio.run(main())
