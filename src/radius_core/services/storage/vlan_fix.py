"""Функции для исправления полей vlan в Redis индексах."""

import json
import logging
from ...clients import execute_redis_command

logger = logging.getLogger(__name__)


async def fix_vlan_fields_in_index(redis, index: str) -> None:
    """Исправляет поля vlan в поврежденном индексе, конвертируя числа в строки"""
    try:
        logger.info("Attempting to fix vlan fields in index: %s", index)

        # Ищем все документы с числовыми полями vlan
        # Сначала попробуем найти документы с числовым vlan
        search_query = "@vlan:{[0-9]*}"
        try:
            result = await execute_redis_command(
                redis, "FT.SEARCH", index, search_query
            )
            if result and result[0] > 0:
                logger.info("Found %s documents with numeric vlan fields", result[0])

                # Обрабатываем каждый найденный документ
                for i in range(result[0]):
                    doc_key = result[2 + i * 2 - 1]  # Ключ документа
                    doc_data = result[2 + i * 2]  # Данные документа

                    if (
                        isinstance(doc_data, list)
                        and len(doc_data) >= 2
                        and doc_data[0] == "$"
                    ):
                        json_str = doc_data[1]
                        if isinstance(json_str, bytes):
                            json_str = json_str.decode("utf-8")

                        try:
                            doc_dict = json.loads(json_str)
                            if "vlan" in doc_dict and isinstance(
                                doc_dict["vlan"], (int, float)
                            ):
                                # Конвертируем vlan в строку
                                old_vlan = doc_dict["vlan"]
                                doc_dict["vlan"] = str(old_vlan)

                                # Обновляем документ в Redis
                                await execute_redis_command(
                                    redis,
                                    "JSON.SET",
                                    doc_key,
                                    "$",
                                    json.dumps(doc_dict),
                                )
                                logger.info(
                                    "Fixed vlan field in document %s: %s -> %s",
                                    doc_key,
                                    old_vlan,
                                    doc_dict["vlan"],
                                )
                        except Exception as e:
                            logger.warning("Failed to fix document %s: %s", doc_key, e)

        except Exception as e:
            logger.warning("Could not search for numeric vlan fields: %s", e)

        logger.info("Vlan field fix attempt completed for index: %s", index)

    except Exception as e:
        logger.error("Error fixing vlan fields in index %s: %s", index, e)
