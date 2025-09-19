"""Операции аккаунтинга RADIUS."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException

from radius_core.services.monitoring.service_checker import (
    check_and_correct_service_state,
)
from ...utils.data_prepare import get_username_onu_mac_vlan_from_data

from ...models.schemas import (
    EnrichedSessionData,
    VideoLoginSearchResult,
)

from ...config import RADIUS_SESSION_PREFIX
from ..storage.queue_operations import (
    send_to_session_queue,
    send_to_traffic_queue,
)

from ..storage.redis_operations import (
    get_session_from_redis,
    save_session_to_redis,
    delete_session_from_redis,
    _check_login_exists_in_redis,
)

from ..storage.search_operations import (
    find_login_by_session,
    get_camera_login_from_redis,
)

from ..data_processing import (
    enrich_session_with_login,
    process_traffic_data,
)

from ..storage.service_operations import (
    update_main_session_service,
)

from ...models import AccountingData, AccountingResponse, SessionData
from ..coa.coa_operations import send_coa_session_kill
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("radius", "process_accounting")
async def process_accounting(
    session_req: AccountingData, redis, rabbitmq
) -> AccountingResponse:
    """Основная функция обработки аккаунтинга"""

    try:
        (
            username,
            onu_mac,
            vlan,
            is_mac_username,
        ) = await get_username_onu_mac_vlan_from_data(session_req)
        is_service_session = bool(session_req.ERX_Service_Session)
        redis_session_key = (
            f"{RADIUS_SESSION_PREFIX}{session_req.Acct_Unique_Session_Id}"
        )
        event_timestamp = int(
            session_req.Event_Timestamp.astimezone(timezone.utc).timestamp()
        )

        logger.info(
            "Аккаунтинг %s для сессии %s",
            session_req.Acct_Status_Type,
            session_req.Acct_Unique_Session_Id,
        )

        # Получаем активную сессию из Redis и данные логина
        session_stored, login = await asyncio.gather(
            get_session_from_redis(redis_session_key, redis),
            find_login_by_session(username, onu_mac, vlan, is_mac_username, redis),
        )

        if login and login.auth_type == "VIDEO":
            # Получаем логин камеры из Redis
            camera_login = await get_camera_login_from_redis(login, redis)
            if camera_login:
                login.login = camera_login

        # Если логин не найден, используем "фейковую" модель с известными параметрами
        login_data = login
        if not login_data:
            params = {"vlan": vlan, "onu_mac": onu_mac}
            if is_mac_username:
                params["mac"] = username
            login_data = VideoLoginSearchResult(**params)

        # Обогащаем данные сессии информацией о логине
        session_req = await enrich_session_with_login(session_req, login_data)

        # Соединяем счетчики трафика
        session_req = process_traffic_data(session_req)

        # Обработка сервисных сессий
        if is_service_session and session_req.Acct_Status_Type == "Start":
            await asyncio.sleep(0.3)
            logger.info(
                "Добавление сервиса в основную сессию таймаут 0.3 секунды (%s) %s",
                session_req.Acct_Status_Type,
                session_req.Acct_Session_Id,
            )
            asyncio.create_task(update_main_session_service(session_req, redis))

        # Обработка завершения сессии при изменении логина или его отсутствии
        if (
            session_req.Acct_Status_Type != "Stop"
            and session_stored
            and session_req.auth_type != "VIDEO"
        ):
            result = await _handle_session_closure_conditions(
                redis,
                rabbitmq,
                redis_session_key,
                session_stored,
                session_req,
                session_req.Event_Timestamp,
                session_req.Acct_Unique_Session_Id,
            )
            if result:
                return result

        # Проверка состояния сервисов для сервисных сессий update
        if (
            login
            and is_service_session
            and session_req.Acct_Status_Type == "Interim-Update"
            and login.auth_type != "VIDEO"
        ):
            try:
                correction_result = await check_and_correct_service_state(
                    session_req, login, login.login, rabbitmq
                )
                if correction_result:
                    logger.info(
                        "Выполнена коррекция сервисов для сессии %s - %s: %s",
                        login.login,
                        session_req.Acct_Unique_Session_Id,
                        correction_result,
                    )
            except Exception as e:
                logger.error(
                    "Ошибка при проверке состояния сервисов для сессии %s: %s",
                    session_req.Acct_Unique_Session_Id,
                    e,
                    exc_info=True,
                )

        # Создаем или обновляем сессию
        session_new = _prepare_session_data(session_stored, session_req)

        # Обработка по типу пакета
        if session_req.Acct_Status_Type == "Start":
            await _handle_start_packet(
                session_new,
                session_req.Event_Timestamp,
                redis_session_key,
                redis,
                is_service_session,
            )
        elif session_req.Acct_Status_Type == "Interim-Update":
            await _handle_interim_update_packet(
                session_new,
                session_stored,
                session_req.Event_Timestamp,
                event_timestamp,
                is_service_session,
                redis_session_key,
                redis,
            )
        elif session_req.Acct_Status_Type == "Stop":
            await _handle_stop_packet(
                session_new,
                session_stored,
                session_req.Event_Timestamp,
                event_timestamp,
                redis_session_key,
                redis,
            )
        else:
            logger.error("Неизвестный тип пакета: %s", session_req.Acct_Status_Type)
            raise HTTPException(
                status_code=400,
                detail=f"Unknown packet type: {session_req.Acct_Status_Type}",
            )

        logger.info("Аккаунтинг успешно обработан: %s", session_req.Acct_Status_Type)
        return AccountingResponse(
            action="noop",
            reason="processed successfully",
            session_id=session_req.Acct_Unique_Session_Id,
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
) -> Optional[AccountingResponse]:
    """Обрабатывает условий для завершения сессии"""
    stored_login = session_stored.login
    current_login = session_req.login

    # 1. Существующая сессия авторизована, а текущая нет
    if stored_login and not current_login:
        logger.warning(
            "Сессия %s найдена авторизованная, но сейчас логин %s не найден. ",
            session_unique_id,
            stored_login,
        )

        # Проверяем, существует ли логин в Redis
        login_exists = await _check_login_exists_in_redis(stored_login, redis)
        if login_exists:
            logger.info("Логин %s найден в Redis, завершаем сессию", stored_login)
            return await _merge_and_close_session(
                session_stored,
                session_req,
                redis_key,
                redis,
                event_time,
                session_unique_id,
                rabbitmq=rabbitmq,
                send_coa=True,
                log_msg=f"Сессия {session_unique_id} завершена: login not found but session authorized before",
                reason="login not found but session authorized before",
            )
        else:
            logger.info("Логин %s не найден в Redis, просто логируем", stored_login)

    # 2. Логин изменился
    elif current_login and stored_login and stored_login != current_login:
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
    elif not stored_login and current_login:
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
    else:
        return None


def _prepare_session_data(
    session_stored: SessionData | None,
    session_req: EnrichedSessionData,
) -> SessionData:
    """Подготавливает данные сессии для обработки"""
    if session_stored:
        session_stored_dict = session_stored.model_dump(by_alias=True)
        session_req_dict = session_req.model_dump(by_alias=True)

        stored_login = session_stored_dict.get("login", None)
        stored_mac = session_stored_dict.get("mac", None)
        stored_vlan = session_stored_dict.get("vlan", None)
        stored_onu_mac = session_stored_dict.get("onu_mac", None)
        stored_erx_service_session = session_stored_dict.get(
            "ERX-Service-Session", None
        )

        session_stored_dict.update(session_req_dict)

        # При Stop и изменении логина возвращаем старые значения login, mac, vlan, onu_mac
        if session_req.Acct_Status_Type == "Stop" and stored_login != session_req.login:
            session_stored_dict["login"] = stored_login
            session_stored_dict["mac"] = stored_mac
            session_stored_dict["vlan"] = stored_vlan
            session_stored_dict["onu_mac"] = stored_onu_mac

        session_stored_dict["ERX-Service-Session"] = stored_erx_service_session
        return SessionData(**session_stored_dict)
    else:
        session_req_dict = session_req.model_dump(by_alias=True)
        return SessionData(**session_req_dict)


async def _handle_start_packet(
    session_new: SessionData,
    event_time: datetime,
    redis_key: str,
    redis,
    is_service_session: bool,
) -> None:
    """Обрабатывает пакет START"""
    logger.info("Обработка пакета START")
    session_new.Acct_Start_Time = event_time
    session_new.Acct_Update_Time = event_time
    session_new.Acct_Session_Time = 0
    session_new.Acct_Stop_Time = None

    if not is_service_session:
        await save_session_to_redis(session_new, redis_key, redis)

    asyncio.create_task(
        _send_to_queue_with_logging(send_to_session_queue, session_new, "session_queue")
    )


async def _handle_interim_update_packet(
    session_new: SessionData,
    session_stored: SessionData | None,
    event_time: datetime,
    event_timestamp: int,
    is_service_session: bool,
    redis_key: str,
    redis,
) -> None:
    """Обрабатывает пакет Interim-Update"""
    if not session_stored:
        logger.info("Interim-Update без активной сессии, создание новой")
        session_new.Acct_Start_Time = datetime.fromtimestamp(
            event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
        )

    session_new.Acct_Update_Time = event_time
    if not is_service_session:
        await save_session_to_redis(session_new, redis_key, redis)

    # Отправляем во все очереди
    asyncio.create_task(
        _send_to_queue_with_logging(
            lambda data: send_to_traffic_queue(data, session_stored),
            session_new,
            "traffic_queue",
        )
    )
    asyncio.create_task(
        _send_to_queue_with_logging(send_to_session_queue, session_new, "session_queue")
    )


async def _handle_stop_packet(
    session_new: SessionData,
    session_stored: SessionData | None,
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
            lambda data: send_to_traffic_queue(data, session_stored),
            session_new,
            "traffic_queue",
        )
    )


async def _merge_and_close_session(
    session_stored: SessionData,
    session_req: EnrichedSessionData,
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
            lambda data: send_to_traffic_queue(data, session_stored),
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
            # logger.debug("Сообщение успешно отправлено в %s", queue_name)
            pass
        else:
            logger.warning("Сообщение не отправлено в %s (вернулся False)", queue_name)
    except Exception as e:
        logger.error("Ошибка отправки в %s: %s", queue_name, e, exc_info=True)
