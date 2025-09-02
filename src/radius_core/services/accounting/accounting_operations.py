"""Операции аккаунтинга RADIUS."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import HTTPException

from radius_core.models.schemas import EnrichedSessionData, LoginSearchResult

from ...config import RADIUS_SESSION_PREFIX
from ..storage.queue_operations import (
    send_to_session_queue,
    send_to_traffic_queue,
)

from ..storage.redis_operations import (
    get_session_from_redis,
    save_session_to_redis,
    delete_session_from_redis,
)

from ..storage.search_operations import (
    find_login_by_session,
)

from ..data_processing import (
    enrich_session_with_login,
    process_traffic_data,
)

from ..storage.service_operations import (
    update_main_session_service,
    update_main_session_from_service,
)

from ...models import AccountingData, AccountingResponse, SessionData
from ..coa.coa_operations import send_coa_session_kill
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("radius", "process_accounting")
async def process_accounting(
    data: AccountingData, redis, rabbitmq
) -> AccountingResponse:
    """Основная функция обработки аккаунтинга"""

    try:
        session_req = data
        packet_type = session_req.Acct_Status_Type
        session_unique_id = session_req.Acct_Unique_Session_Id
        event_time = session_req.Event_Timestamp
        event_timestamp = int(
            session_req.Event_Timestamp.astimezone(timezone.utc).timestamp()
        )

        logger.info(
            "Обработка аккаунтинга: %s для сессии %s", packet_type, session_unique_id
        )

        # Получаем активную сессию из Redis и данные логина
        redis_key = f"{RADIUS_SESSION_PREFIX}{session_unique_id}"
        session_stored, login = await asyncio.gather(
            get_session_from_redis(redis_key, redis),
            find_login_by_session(session_req, redis),
        )

        # Добавляем данные логина в данные сессии
        session_req = await enrich_session_with_login(session_req, login)

        # Соединяем счетчики трафика
        session_req = process_traffic_data(session_req)

        service = session_req.ERX_Service_Session
        is_service_session = bool(service)
        session_id = session_req.Acct_Session_Id

        # Обработка сервисных сессий
        if service:
            logger.debug("Добавление сервиса в основную сессию %s", session_id)
            # Запускаем в фоне, не ждем завершения
            asyncio.create_task(update_main_session_service(session_req, redis))

        # Обработка основных сессий - поиск сервисной сессии
        else:
            logger.debug("Поиск сервисной сессии для основной сессии %s", session_id)
            await update_main_session_from_service(session_req, redis)

        # Обработка завершения сессии при изменении логина или его отсутствии
        if session_stored and packet_type != "Stop":
            session_closure_result = await _handle_session_closure_conditions(
                redis,
                rabbitmq,
                redis_key,
                session_stored,
                session_req,
                event_time,
                session_unique_id,
                login,
            )
            if session_closure_result:
                return session_closure_result

        # Создаем или обновляем сессию
        session_new = _prepare_session_data(
            session_stored, session_req, is_service_session
        )

        # Обработка по типу пакета
        if packet_type == "Start":
            await _handle_start_packet(session_new, event_time, redis_key, redis)
        elif packet_type == "Interim-Update":
            await _handle_interim_update_packet(
                session_new,
                session_stored,
                event_time,
                event_timestamp,
                is_service_session,
                redis_key,
                redis,
            )
        elif packet_type == "Stop":
            await _handle_stop_packet(
                session_new,
                session_stored,
                event_time,
                event_timestamp,
                redis_key,
                redis,
            )
        else:
            logger.error("Неизвестный тип пакета: %s", packet_type)
            raise HTTPException(
                status_code=400, detail=f"Unknown packet type: {packet_type}"
            )

        logger.info("Аккаунтинг успешно обработан: %s", packet_type)
        return AccountingResponse(
            action="noop", reason="processed successfully", session_id=session_unique_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка при обработке аккаунтинга: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _handle_session_closure_conditions(
    redis,
    rabbitmq,
    redis_key: str,
    session_stored: SessionData,
    session_req: EnrichedSessionData,
    event_time: datetime,
    session_unique_id: str,
    login: LoginSearchResult | None = None,
) -> Optional[AccountingResponse]:
    """Обрабатывает условий для завершения сессии"""
    stored_login = session_stored.login
    # stored_auth_type = getattr(session_stored, "auth_type", "UNAUTH")
    current_login = login.login if login else None
    # current_auth_type = getattr(login, "auth_type", "UNAUTH")

    # 1. Существующая сессия авторизована, а текущая нет
    if stored_login and not current_login:
        logger.warning(
            "Сессия %s найдена авторизованная, но сейчас логин %s не найден. ",
            session_unique_id,
            stored_login,
        )
        # return await _merge_and_close_session(
        #     session_stored,
        #     session_req,
        #     redis_key,
        #     redis,
        #     event_time,
        #     session_unique_id,
        #     rabbitmq=rabbitmq,
        #     send_coa=True,
        #     log_msg=f"Сессия {session_unique_id} завершена: login not found but session authorized before",
        #     reason="login not found but session authorized before",
        # )

    # 2. Логин изменился
    if current_login and stored_login and stored_login != current_login:
        logger.warning(
            "Логин изменился с %s на %s, завершаем сессию. %s",
            stored_login,
            current_login,
            session_unique_id,
        )
        return await _merge_and_close_session(
            session_stored,
            session_req,
            redis_key,
            redis,
            event_time,
            session_unique_id,
            rabbitmq=rabbitmq,
            send_coa=True,
            log_msg=f"Сессия {session_unique_id} завершена: login mismatch",
            reason="login mismatch",
        )

    # 3. Сессия была UNAUTH, теперь авторизована
    if not stored_login and current_login:
        logger.warning(
            "Сессия %s была UNAUTH, теперь логин %s авторизован",
            session_unique_id,
            current_login,
        )
        return await _merge_and_close_session(
            session_stored,
            session_req,
            redis_key,
            redis,
            event_time,
            session_unique_id,
            rabbitmq=rabbitmq,
            send_coa=True,
            log_msg=f"Сессия {session_unique_id} завершена: UNAUTH -> AUTH",
            reason="session UNAUTH closed (now authorized)",
        )

    return None


def _prepare_session_data(
    session_stored: SessionData | None,
    session_req: EnrichedSessionData,
    is_service_session: bool,
) -> SessionData:
    """Подготавливает данные сессии для обработки"""
    if session_stored:
        logger.debug("Обогащение существующей сессии новыми данными")
        session_stored_dict = session_stored.model_dump(by_alias=True)
        session_req_dict = session_req.model_dump(by_alias=True)

        old_login = session_stored_dict.get("login", None)

        # Для несервисных сессий не обновляем ERX_Service_Session
        if not is_service_session and "ERX-Service-Session" in session_req_dict:
            session_req_dict.pop("ERX-Service-Session")

        if old_login != session_req.login:
            logger.debug(
                "Логин изменился с %s на %s в STOP пакете - взяли старый логин %s",
                old_login,
                session_req.login,
                session_req.Acct_Unique_Session_Id,
            )
            session_req_dict.pop("login")

        session_stored_dict.update(session_req_dict)
        return SessionData(**session_stored_dict)
    else:
        logger.debug("Создание новой сессии из входящих данных")
        session_req_dict = session_req.model_dump(by_alias=True)
        if not is_service_session and "ERX-Service-Session" in session_req_dict:
            session_req_dict.pop("ERX-Service-Session")
        return SessionData(**session_req_dict)


async def _handle_start_packet(
    session_new: SessionData, event_time: datetime, redis_key: str, redis
) -> None:
    """Обрабатывает пакет START"""
    logger.info("Обработка пакета START")
    session_new.Acct_Start_Time = event_time
    session_new.Acct_Update_Time = event_time
    session_new.Acct_Session_Time = 0
    session_new.Acct_Stop_Time = None

    await save_session_to_redis(session_new, redis_key, redis)

    asyncio.create_task(
        _send_to_queue_with_logging(send_to_session_queue, session_new, "session_queue")
    )


async def _handle_interim_update_packet(
    session_new: SessionData,
    session_stored: Any,
    event_time: datetime,
    event_timestamp: int,
    is_service_session: bool,
    redis_key: str,
    redis,
) -> None:
    """Обрабатывает пакет Interim-Update"""
    if session_stored:
        # Обновляем существующую сессию
        logger.debug("Обработка Interim-Update для существующей сессии")
        session_new.Acct_Update_Time = event_time
        await save_session_to_redis(session_new, redis_key, redis)
    else:
        # Создаем новую сессию
        logger.info("Interim-Update без активной сессии, создание новой")
        session_new.Acct_Start_Time = datetime.fromtimestamp(
            event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
        )
        session_new.Acct_Update_Time = event_time

        # Сохраняем в Redis
        # if not is_service_session:
        await save_session_to_redis(session_new, redis_key, redis)

    # Отправляем во все очереди
    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_traffic_queue(
                data, session_stored if session_stored else None
            ),
            session_new,
            "traffic_queue",
        )
    )
    asyncio.create_task(
        _send_to_queue_with_logging(send_to_session_queue, session_new, "session_queue")
    )


async def _handle_stop_packet(
    session_new: SessionData,
    session_stored: Any,
    event_time: datetime,
    event_timestamp: int,
    redis_key: str,
    redis,
) -> None:
    """Обрабатывает пакет STOP"""
    logger.info("Обработка пакета STOP")
    session_new.Acct_Stop_Time = event_time
    session_new.Acct_Update_Time = event_time

    if not session_stored:
        session_new.Acct_Start_Time = datetime.fromtimestamp(
            event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
        )
    else:
        session_new.Acct_Start_Time = session_stored.Acct_Start_Time

    if session_stored:
        await delete_session_from_redis(redis_key, redis)

    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_session_queue(data, stoptime=True),
            session_new,
            "session_queue",
        )
    )
    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_traffic_queue(
                data, session_stored if session_stored else None
            ),
            session_new,
            "traffic_queue",
        )
    )


async def _merge_and_close_session(
    session_stored: Any,
    session_req: Any,
    redis_key: str,
    redis,
    event_time: datetime,
    session_unique_id: str,
    rabbitmq=None,
    send_coa: bool = False,
    log_msg: Optional[str] = None,
    reason: str = "",
) -> AccountingResponse:
    """Слить данные входящего пакета в существующую сессию,
    опционально отправить CoA kill и удалить сессию из Redis"""
    # Merge models
    session_stored_dict = session_stored.model_dump(by_alias=True)
    session_req_dict = session_req.model_dump(by_alias=True)
    session_stored_dict.update(session_req_dict)
    session_to_close = SessionData(**session_stored_dict)

    session_to_close.Acct_Stop_Time = event_time
    session_to_close.Acct_Update_Time = event_time

    await delete_session_from_redis(redis_key, redis)

    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_session_queue(data, stoptime=True),
            session_to_close,
            "session_queue",
        )
    )
    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_traffic_queue(
                data, session_stored if session_stored else None
            ),
            session_to_close,
            "traffic_queue",
        )
    )

    if send_coa:
        asyncio.create_task(
            _send_to_queue_with_logging(
                lambda data: send_coa_session_kill(data, rabbitmq, reason=reason),
                session_to_close,
                "coa_queue",
            )
        )

    if log_msg:
        logger.info(log_msg)
    else:
        logger.info("Сессия %s сохранена и завершена (%s)", session_unique_id, reason)

    return AccountingResponse(
        action="kill", reason=reason, session_id=session_unique_id
    )


async def _send_to_queue_with_logging(queue_func, data, queue_name: str):
    """Отправка в очередь с логированием ошибок без блокировки"""
    try:
        result = await queue_func(data)
        if result:
            logger.debug("Сообщение успешно отправлено в %s", queue_name)
        else:
            logger.warning("Сообщение не отправлено в %s (вернулся False)", queue_name)
    except Exception as e:
        logger.error("Ошибка отправки в %s: %s", queue_name, e, exc_info=True)
