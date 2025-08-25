#!/usr/bin/env python3
"""
Скрипт для быстрого исправления всех документов login:* с числовыми полями vlan.
Конвертирует числовые значения vlan в строки для совместимости с RediSearch индексом.

Работает в три этапа:
1. Сканирование: анализирует все документы и находит те, у которых vlan числовой
2. Обновление: исправляет найденные документы (конвертирует vlan в строки)
3. Пересоздание индекса: удаляет старый индекс и создает новый с правильной схемой

Запуск:
    # Полный цикл (сканирование + обновление + пересоздание индекса)
    python fix_vlan_script.py

    # Только сканирование (без обновления)
    DRY_RUN=true python fix_vlan_script.py

    # С настройками
    BATCH_SIZE=200 SCAN_RESULTS_FILE=my_results.json python fix_vlan_script.py

Переменные окружения:
    BATCH_SIZE - размер пачки для обработки (по умолчанию: 100)
    SCAN_RESULTS_FILE - файл для сохранения результатов сканирования
    DRY_RUN - только сканирование без обновления (true/false)
    REDIS_URL - URL подключения к Redis

Требования:
    - redis.asyncio
    - tqdm
    - Доступ к Redis серверу
"""

import asyncio
import json
import logging
import sys
from typing import List, Tuple, Any
import redis.asyncio as redis
from tqdm import tqdm
import os

# Импортируем конфигурацию из проекта
sys.path.append("src")
from radius_core.config.settings import REDIS_URL, RADIUS_INDEX_NAME


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

# Размер пачки для mget операций (можно переопределить через переменную окружения)
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

# Файл для сохранения результатов сканирования
SCAN_RESULTS_FILE = os.getenv("SCAN_RESULTS_FILE", "vlan_scan_results.json")

# Сухой запуск (только сканирование без обновления)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")


async def get_redis_connection():
    """Создает подключение к Redis"""
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


async def scan_login_keys(redis_client) -> List[str]:
    """Сканирует все ключи login:* в Redis"""
    logger.info("Сканирование ключей login:*...")
    login_keys = []
    cursor = 0

    try:
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor, match="login:*", count=1000
            )
            login_keys.extend([key.decode("utf-8") for key in keys])

            if cursor == 0:
                break

        logger.info("Найдено %s ключей login:*", len(login_keys))
        return login_keys
    except Exception as e:
        logger.error("Ошибка при сканировании ключей: %s", e)
        return []


async def scan_vlan_fields(
    redis_client, keys: List[str]
) -> List[Tuple[str, dict, Any]]:
    """Сканирует документы и находит те, у которых vlan числовой"""
    numeric_vlan_keys = []

    try:
        # Получаем все документы одной командой
        docs = await redis_client.json().mget(keys, "$")

        # Анализируем документы
        for key, doc_list in zip(keys, docs):
            if doc_list and len(doc_list) > 0:
                doc_data = doc_list[0]
                if "vlan" in doc_data and isinstance(doc_data["vlan"], (int, float)):
                    numeric_vlan_keys.append((key, doc_data, doc_data["vlan"]))

        return numeric_vlan_keys

    except Exception as e:
        logger.warning("Ошибка при сканировании пачки документов: %s", e)
        return []


async def fix_vlan_field_batch(
    redis_client, docs_batch: List[Tuple[str, dict, Any]]
) -> List[str]:
    """Исправляет поля vlan в пачке документов"""
    fixed_keys = []

    try:
        # Подготавливаем команды для обновления
        update_commands = []
        for key, doc_data, old_vlan in docs_batch:
            # Конвертируем vlan в строку
            doc_data["vlan"] = str(old_vlan)

            # Добавляем команду обновления
            update_commands.append((key, doc_data))
            fixed_keys.append(key)

            logger.debug(
                "Подготовлено исправление %s: vlan %s -> '%s'",
                key,
                old_vlan,
                doc_data["vlan"],
            )

        # Выполняем обновления пачкой
        if update_commands:
            pipeline = redis_client.pipeline()
            for key, doc_data in update_commands:
                pipeline.json().set(key, "$", doc_data)

            # Выполняем все команды
            await pipeline.execute()

            logger.info("Исправлено %s документов в пачке", len(fixed_keys))

        return fixed_keys

    except Exception as e:
        logger.warning("Ошибка при исправлении пачки документов: %s", e)
        return []


async def recreate_index(redis_client, index_name: str = None):
    """Пересоздает RediSearch индекс"""
    if index_name is None:
        index_name = RADIUS_INDEX_NAME

    try:
        # Удаляем старый индекс
        try:
            await redis_client.execute_command("FT.DROPINDEX", index_name)
            logger.info("Старый индекс %s удален", index_name)
        except Exception as e:
            if "Unknown Index name" in str(e):
                logger.info("Индекс %s не существовал, создаем новый", index_name)
            else:
                logger.warning("Ошибка при удалении индекса %s: %s", index_name, e)

        # Создаем новый индекс
        create_command = [
            "FT.CREATE",
            index_name,
            "ON",
            "JSON",
            "PREFIX",
            "1",
            "login:",
            "SCHEMA",
            "$.onu_mac",
            "AS",
            "onu_mac",
            "TAG",
            "$.mac",
            "AS",
            "mac",
            "TAG",
            "$.ip_addr",
            "AS",
            "ip_addr",
            "TAG",
            "$.vlan",
            "AS",
            "vlan",
            "TAG",
        ]

        await redis_client.execute_command(*create_command)
        logger.info("Новый индекс %s создан", index_name)

    except Exception as e:
        logger.error("Ошибка при пересоздании индекса %s: %s", index_name, e)
        raise


async def save_scan_results(
    numeric_vlan_docs: List[Tuple[str, dict, Any]], filename: str
):
    """Сохраняет результаты сканирования в JSON файл"""
    try:
        # Подготавливаем данные для сохранения
        results = []
        for key, doc_data, old_vlan in numeric_vlan_docs:
            results.append(
                {
                    "key": key,
                    "old_vlan": old_vlan,
                    "new_vlan": str(old_vlan),
                    "doc_size": len(str(doc_data)),
                    "has_other_numeric_fields": any(
                        isinstance(v, (int, float))
                        for k, v in doc_data.items()
                        if k != "vlan"
                    ),
                }
            )

        # Сохраняем в файл
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        logger.info("Результаты сканирования сохранены в %s", filename)
        logger.info("Найдено %s документов с числовыми полями vlan", len(results))

        # Показываем статистику
        if results:
            vlan_values = [r["old_vlan"] for r in results]
            logger.info(
                "Диапазон значений vlan: %s - %s", min(vlan_values), max(vlan_values)
            )
            logger.info("Уникальных значений vlan: %s", len(set(vlan_values)))

    except Exception as e:
        logger.warning("Ошибка при сохранении результатов сканирования: %s", e)


async def main():
    """Основная функция"""
    logger.info("Запуск скрипта исправления полей vlan...")

    # Логируем конфигурацию
    if REDIS_URL:
        logger.info("Используется REDIS_URL из конфигурации: %s", REDIS_URL)
    else:
        logger.info("Используется fallback конфигурация: %s:%s", REDIS_HOST, REDIS_PORT)

    logger.info("Используется индекс: %s", RADIUS_INDEX_NAME)
    logger.info("Размер пачки: %s", BATCH_SIZE)

    # Подключаемся к Redis
    redis_client = await get_redis_connection()

    try:
        # Сканируем все ключи login:*
        login_keys = await scan_login_keys(redis_client)
        if not login_keys:
            logger.warning("Ключи login:* не найдены")
            return

        total_count = len(login_keys)
        logger.info("Найдено %s ключей login:*. Начинаем сканирование...", total_count)

        # ЭТАП 1: Сканируем все документы и находим те, у которых vlan числовой
        numeric_vlan_docs = []

        logger.info("Этап 1: Сканирование документов с числовыми полями vlan...")
        with tqdm(
            total=total_count, desc="Сканирование документов", unit="doc"
        ) as pbar:
            for i in range(0, total_count, BATCH_SIZE):
                batch_keys = login_keys[i : i + BATCH_SIZE]

                # Сканируем пачку документов
                batch_numeric_vlan = await scan_vlan_fields(redis_client, batch_keys)
                numeric_vlan_docs.extend(batch_numeric_vlan)

                # Обновляем прогресс
                pbar.update(len(batch_keys))

                # Показываем промежуточную статистику
                if (i + BATCH_SIZE) % (
                    BATCH_SIZE * 10
                ) == 0 or i + BATCH_SIZE >= total_count:
                    logger.info(
                        "Просканировано: %s/%s (%.1f%%), Найдено с числовым vlan: %s",
                        min(i + BATCH_SIZE, total_count),
                        total_count,
                        min(i + BATCH_SIZE, total_count) / total_count * 100,
                        len(numeric_vlan_docs),
                    )

        logger.info(
            "Сканирование завершено! Найдено %s документов с числовыми полями vlan",
            len(numeric_vlan_docs),
        )

        # Сохраняем результаты сканирования
        if numeric_vlan_docs:
            await save_scan_results(numeric_vlan_docs, SCAN_RESULTS_FILE)

        if not numeric_vlan_docs:
            logger.info(
                "Документы с числовыми полями vlan не найдены. Ничего исправлять не нужно."
            )
            return

        # ЭТАП 2: Обновляем найденные документы
        if DRY_RUN:
            logger.info("Режим DRY_RUN: обновление документов пропущено")
            logger.info("Для выполнения обновлений запустите скрипт без DRY_RUN=true")
            return

        logger.info("Этап 2: Обновление документов с числовыми полями vlan...")
        fixed_count = 0

        with tqdm(
            total=len(numeric_vlan_docs), desc="Обновление документов", unit="doc"
        ) as pbar:
            for i in range(0, len(numeric_vlan_docs), BATCH_SIZE):
                batch_docs = numeric_vlan_docs[i : i + BATCH_SIZE]

                # Исправляем поля vlan в пачке
                fixed_keys = await fix_vlan_field_batch(redis_client, batch_docs)
                fixed_count += len(fixed_keys)

                # Обновляем прогресс
                pbar.update(len(batch_docs))

                # Показываем промежуточную статистику
                if (i + BATCH_SIZE) % (BATCH_SIZE * 10) == 0 or i + BATCH_SIZE >= len(
                    numeric_vlan_docs
                ):
                    logger.info(
                        "Обновлено: %s/%s (%.1f%%), Всего исправлено: %s",
                        min(i + BATCH_SIZE, len(numeric_vlan_docs)),
                        len(numeric_vlan_docs),
                        min(i + BATCH_SIZE, len(numeric_vlan_docs))
                        / len(numeric_vlan_docs)
                        * 100,
                        fixed_count,
                    )

        logger.info(
            "Обновление завершено! Исправлено %s из %s документов с числовыми полями vlan",
            fixed_count,
            len(numeric_vlan_docs),
        )

        # ЭТАП 3: Пересоздаем индекс
        if fixed_count > 0:
            logger.info("Этап 3: Пересоздание RediSearch индекса...")
            await recreate_index(redis_client, RADIUS_INDEX_NAME)
            logger.info("Индекс успешно пересоздан")

            logger.info(
                "Рекомендуется перезапустить приложение для применения изменений"
            )

    except KeyboardInterrupt:
        logger.info("Скрипт прерван пользователем")
    except Exception as e:
        logger.error("Критическая ошибка: %s", e)
    finally:
        # Закрываем подключение
        await redis_client.close()
        logger.info("Подключение к Redis закрыто")


if __name__ == "__main__":
    # Запускаем асинхронную функцию
    asyncio.run(main())
