"""Операции для работы с сервисами."""

import json
import logging

from ...models import EnrichedSessionData
from ...clients import execute_redis_command

logger = logging.getLogger(__name__)


async def update_main_session_service(
    service_session_req: EnrichedSessionData, redis
) -> bool:
    """
    Обновляет поле ERX-Service-Session в основной сессии на основе данных сервисной сессии.
    Args:
        service_session_req: Данные сервисной сессии
        redis: Redis client
    Returns:
        bool: True если обновление прошло успешно, False если основная сессия не найдена
    """
    try:
        service_session_id = service_session_req.Acct_Session_Id

        # Извлекаем основной ID из сервисного (берем часть до двоеточия)
        if ":" in service_session_id:
            main_session_id = service_session_id.split(":")[0]
        else:
            logger.warning(
                "Неожиданный формат ID сервисной сессии: %s", service_session_id
            )
            return False

        logger.debug(
            "Поиск основной сессии по Acct-Session-Id: %s -> %s",
            service_session_id,
            main_session_id,
        )

        # Поиск основной сессии по полю Acct-Session-Id в индексе
        index = "idx:radius:session"
        query = f"@Acct\\-Session\\-Id:{{{main_session_id}}}"
        logger.debug("Поиск основной сессии в индексе %s с запросом %s", index, query)

        # Получаем основную сессию из Redis
        result = await execute_redis_command(redis, "FT.SEARCH", index, query)

        logger.debug(
            "FT.SEARCH result for main_session_id=%s: %s", main_session_id, result
        )

        if result and result[0] > 0:
            num_results = result[0]
            logger.info(
                "Найдено %s сессий по Acct-Session-Id %s", num_results, main_session_id
            )
            for i in range(num_results):
                try:
                    fields = result[2 + i * 2]
                    logger.debug("fields[%s]: %s", i, fields)
                    if (
                        isinstance(fields, list)
                        and len(fields) >= 2
                        and fields[0] == "$"
                    ):
                        doc_data = fields[1]
                        logger.debug("doc_data[%s]: %s", i, doc_data)
                        if isinstance(doc_data, bytes):
                            doc_data = doc_data.decode("utf-8")
                        logger.debug("doc_data[%s] (decoded): %s", i, doc_data)
                        session_dict = json.loads(doc_data)
                        logger.debug("session_dict[%s]: %s", i, session_dict)
                        session_key = result[2 + i * 2 - 1]
                        logger.debug("session_key[%s]: %s", i, session_key)

                        # Обновляем поле ERX-Service-Session
                        service_session_value = service_session_req.ERX_Service_Session
                        if service_session_value:
                            logger.info(
                                "Обновление ERX-Service-Session в основной сессии %s: %s",
                                session_key,
                                service_session_value,
                            )
                            await execute_redis_command(
                                redis,
                                "JSON.SET",
                                session_key,
                                "$.ERX-Service-Session",
                                json.dumps(service_session_value),
                            )
                            return True
                        else:
                            logger.warning(
                                "ERX-Service-Session пустой в сервисной сессии %s",
                                service_session_id,
                            )
                            return False

                except Exception as e:
                    logger.error("Ошибка обработки результата %s: %s", i, e)
                    continue

            logger.warning(
                "Не удалось обновить основную сессию для %s", main_session_id
            )
            return False
        else:
            logger.warning(
                "Основная сессия не найдена по Acct-Session-Id %s", main_session_id
            )
            return False

    except Exception as e:
        logger.error("Ошибка при обновлении основной сессии: %s", e)
        return False
