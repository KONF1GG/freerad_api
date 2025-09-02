"""Операции для работы с сервисами."""

import json
import logging

from radius_core.config.settings import RADIUS_INDEX_NAME_SESSION

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

        # Извлекаем основной ID из сервисного
        if ":" in service_session_id:
            main_session_id = service_session_id.split(":")[0]
        else:
            logger.warning(
                "Неожиданный формат ID сервисной сессии: %s", service_session_id
            )
            return False

        logger.info(
            "Поиск основной сессии по Acct-Session-Id: %s -> %s",
            service_session_id,
            main_session_id,
        )

        # Поиск основной сессии по полю Acct-Session-Id в индексе
        index = RADIUS_INDEX_NAME_SESSION
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
                "SERVICE SESSION: Найдено %s сессий по Acct-Session-Id %s", num_results, main_session_id
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
                                "SERVICE SESSION: Обновление ERX-Service-Session в основной сессии %s: %s",
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
                                "SERVICE SESSION: ERX-Service-Session пустой в сервисной сессии %s",
                                service_session_id,
                            )
                            return False

                except Exception as e:
                    logger.error("SERVICE SESSION: Ошибка обработки результата %s: %s", i, e)
                    continue

            logger.warning(
                "SERVICE SESSION: Не удалось обновить основную сессию для %s", main_session_id
            )
            return False
        else:
            logger.warning(
                "SERVICE SESSION: Основная сессия не найдена по Acct-Session-Id %s", main_session_id
            )
            return False

    except Exception as e:
        logger.error("SERVICE SESSION: Ошибка при обновлении основной сессии: %s", e)
        return False


async def update_main_session_from_service(
    main_session_req: EnrichedSessionData, redis
) -> bool:
    """
    Находит сервисную сессию для основной сессии и обновляет поле ERX-Service-Session в session_req.
    Args:
        main_session_req: Данные основной сессии (будет обновлен)
        redis: Redis client
    Returns:
        bool: True если обновление прошло успешно, False если сервисная сессия не найдена
    """
    try:
        main_session_id = main_session_req.Acct_Session_Id

        logger.info(
            "Поиск сервисной сессии для основной сессии: %s",
            main_session_id,
        )

        # Поиск сервисной сессии по паттерну main_session_id:*
        index = RADIUS_INDEX_NAME_SESSION
        query = f"@Acct\\-Session\\-Id:{{{main_session_id}*}}"
        logger.debug("Поиск сервисной сессии в индексе %s с запросом %s", index, query)
        # Получаем сервисную сессию из Redis
        result = await execute_redis_command(redis, "FT.SEARCH", index, query)

        logger.debug(
            "FT.SEARCH result for service session pattern %s:*: %s",
            main_session_id,
            result,
        )

        if result and result[0] > 0:
            num_results = result[0]
            logger.info(
                "MIAN SESSION: Найдено %s сервисных сессий для основной сессии %s",
                num_results,
                main_session_id,
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
                        service_session_key = result[2 + i * 2 - 1]
                        logger.debug(
                            "service_session_key[%s]: %s", i, service_session_key
                        )

                        # Получаем поле ERX-Service-Session из сервисной сессии
                        service_session_value = session_dict.get("ERX-Service-Session")
                        if service_session_value:
                            logger.info(
                                "MIAN SESSION: Найдено поле ERX-Service-Session в сервисной сессии %s: %s",
                                service_session_key,
                                service_session_value,
                            )

                            # Обновляем данные в session_req
                            main_session_req.ERX_Service_Session = service_session_value
                            logger.info(
                                "MIAN SESSION: Обновлено поле ERX-Service-Session в session_req: %s",
                                service_session_value,
                            )
                            return True
                        else:
                            logger.warning(
                                "MIAN SESSION: ERX-Service-Session не найден в сервисной сессии %s",
                                service_session_key,
                            )
                            continue

                except Exception as e:
                    logger.error("Ошибка обработки результата %s: %s", i, e)
                    continue

            logger.warning(
                "MIAN SESSION: Не удалось найти сервисную сессию для основной сессии %s",
                main_session_id,
            )
            return False
        else:
            logger.warning(
                "MIAN SESSION: Сервисная сессия не найдена для основной сессии %s", main_session_id
            )
            return False

    except Exception as e:
        logger.error("MIAN SESSION: Ошибка при поиске сервисной сессии: %s", e, exc_info=True)
        return False
