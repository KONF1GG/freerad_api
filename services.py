import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException

from config import RADIUS_SESSION_PREFIX
from crud import (
    ch_save_session,
    ch_save_traffic,
    enrich_session_with_login,
    find_login_by_session,
    find_sessions_by_login,
    get_session_from_redis,
    process_traffic_data,
    save_session_to_redis,
    delete_session_from_redis,
)
from schemas import AccountingData, AccountingResponse, AuthRequest, SessionData
from utils import nasportid_parse

logger = logging.getLogger(__name__)


async def send_coa_session_kill(session_req) -> bool:
    """Отправка команды на убийство сессии через CoA"""
    ...


async def send_coa_session_set(session_req, attributes: Dict[str, Any]) -> bool:
    """Отправка команды на обновление атрибутов сессии через CoA"""
    ...


async def process_accounting(data: AccountingData) -> AccountingResponse:
    """Основная функция обработки аккаунтинга"""
    start_time = time.time()
    status = "success"

    try:
        session_req = data
        packet_type = session_req.Acct_Status_Type
        session_unique_id = session_req.Acct_Unique_Session_Id
        event_time = session_req.Event_Timestamp
        event_timestamp = int(
            session_req.Event_Timestamp.astimezone(timezone.utc).timestamp()
        )

        logger.info(
            f"Обработка аккаунтинга: {packet_type} для сессии {session_unique_id}"
        )
        # Получаем активную сессию из Redis
        redis_key = f"{RADIUS_SESSION_PREFIX}{session_unique_id}"
        session_stored, login = await asyncio.gather(
            get_session_from_redis(redis_key),
            find_login_by_session(session_req),
        )

        # Добавляем данные логина в данные сессии
        session_req = await enrich_session_with_login(session_req, login)

        service = session_req.ERX_Service_Session
        is_service_session = bool(service)

        # Обработка сервисной сессии
        if service and login:
            timeto = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "timeto",
                None,
            )
            speed = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "speed",
                None,
            )

            # Проверяем текущее время для сравнения со сроком действия услуги
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = False

            if timeto is not None:
                try:
                    timeto_float = float(timeto)
                    service_should_be_blocked = timeto_float < now_timestamp
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid timeto value: {timeto}, error: {e}")

            # Анализируем состояние с роутера
            router_says_blocked = "NOINET-NOMONEY" in service

            # Сравниваем состояние с роутера с ожидаемым состоянием
            if router_says_blocked and service_should_be_blocked:
                logger.debug(
                    f"Сервис корректно заблокирован для логина {getattr(login, 'login', 'unknown')}"
                )
            elif router_says_blocked and not service_should_be_blocked:
                # Роутер заблокировал, но услуга должна работать - разблокируем
                logger.warning(
                    f"Роутер неправильно заблокировал услугу для логина {getattr(login, 'login', 'unknown')}, разблокировка"
                )

                # Устанавливаем правильную скорость
                if speed:
                    expected_speed_mb = float(speed) * 1.1
                    coa_attributes = {
                        "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
                    }
                    await send_coa_session_set(session_req, coa_attributes)

                    return AccountingResponse(
                        action="update",
                        reason="router incorrectly blocked service, unblocked",
                        coa_attributes=coa_attributes,
                        session_id=session_unique_id,
                    )
            elif not router_says_blocked and service_should_be_blocked:
                # Роутер не заблокировал, но услуга должна быть заблокирована - блокируем
                logger.warning(
                    f"Услуга {getattr(login, 'login', 'unknown')} должна быть заблокирована, но роутер этого не сделал"
                )
                await send_coa_session_kill(session_req)

                return AccountingResponse(
                    action="kill",
                    reason="service expired",
                    session_id=session_unique_id,
                )
            else:
                # Роутер не заблокировал и услуга не должна быть заблокирована - проверяем скорость
                match = re.search(r"\(([\d.]+k)\)", service)
                if match:
                    service_speed_mb = (
                        float(match.group(1).replace("k", "")) / 1000
                    )  # k -> Mb
                    expected_speed_mb = float(speed) * 1.1 if speed else 0
                    if abs(service_speed_mb - expected_speed_mb) >= 0.01:
                        logger.warning(
                            f"Неправильная скорость: Ожидалось {expected_speed_mb} Mb, получено {service_speed_mb} Mb"
                        )
                        # Обновляем параметры сессии
                        coa_attributes = {
                            "ERX-Cos-Shaping-Rate": int(expected_speed_mb * 1000)
                        }
                        await send_coa_session_set(session_req, coa_attributes)

                        return AccountingResponse(
                            action="update",
                            reason="speed mismatch corrected",
                            coa_attributes=coa_attributes,
                            session_id=session_unique_id,
                        )

        # Проверка смены логина
        if (
            session_stored
            and login
            and hasattr(login, "login")
            and session_stored.login != login.login
        ):
            logger.info(
                f"Логин изменился с {session_stored.login} "
                f"на {login.login}, убиваем сессию"
            )
            await send_coa_session_kill(session_stored.model_dump(by_alias=True))

        # Соединяем счетчики
        session_req = await process_traffic_data(session_req)

        if session_stored:
            logger.info("Обогащение существующей сессии новыми данными")
            session_stored_dict = session_stored.model_dump(by_alias=True)
            session_req_dict = session_req.model_dump(by_alias=True)
            session_stored_dict.update(session_req_dict)
            session_new = SessionData(**session_stored_dict)
        else:
            logger.info("Создание новой сессии из входящих данных")
            session_new = SessionData(**session_req.model_dump(by_alias=True))

        # Обработка по типу пакета
        # Если пакет типа Start, создаем новую сессию
        if packet_type == "Start":
            logger.info("Обработка пакета START")
            session_new.Acct_Start_Time = event_time
            session_new.Acct_Update_Time = event_time
            session_new.Acct_Session_Time = 0
            session_new.Acct_Stop_Time = None

            await asyncio.gather(
                save_session_to_redis(session_new, redis_key),
                ch_save_session(session_new),
            )

        # Если пакет типа Interim-Update
        elif packet_type == "Interim-Update":
            # Если сессия уже существует, обновляем ее

            tasks = []
            if session_stored:
                logger.info("Обработка Interim-Update для существующей сессии")
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, session_stored))
            # Если сессия не существует, создаем новую
            else:
                logger.warning("Interim-Update без активной сессии, создаем новую")
                session_new.Acct_Start_Time = datetime.fromtimestamp(
                    event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
                )
                session_new.Acct_Update_Time = event_time
                tasks.append(ch_save_traffic(session_new, None))

            if not is_service_session:
                tasks.append(save_session_to_redis(session_new, redis_key))
            tasks.append(ch_save_session(session_new))
            await asyncio.gather(*tasks)

        # Если пакет типа Stop, завершаем сессию
        elif packet_type == "Stop":
            logger.info("Обработка пакета STOP")
            session_new.Acct_Stop_Time = event_time
            session_new.Acct_Update_Time = event_time

            if not session_stored:
                session_new.Acct_Start_Time = datetime.fromtimestamp(
                    event_timestamp - session_new.Acct_Session_Time, tz=timezone.utc
                )
            else:
                session_new.Acct_Start_Time = session_stored.Acct_Start_Time

            await asyncio.gather(
                ch_save_session(session_new, stoptime=True),
                ch_save_traffic(
                    session_new,
                    session_stored if session_stored else None,
                ),
                delete_session_from_redis(redis_key)
                if session_stored
                else asyncio.sleep(0),
            )

        else:
            logger.error(f"Неизвестный тип пакета: {packet_type}")
            return AccountingResponse(
                action="log",
                reason=f"Неизвестный тип пакета: {packet_type}",
                status="error",
            )

        logger.info(f"Успешный аккаунтинг: {packet_type}")
        return AccountingResponse(
            action="noop", reason="processed successfully", session_id=session_unique_id
        )
    except Exception as e:
        status = "error"
        logger.error(f"Ошибка при обработке аккаунтинга: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        exec_time = time.time() - start_time
        logger.debug(f"Аккаунтинг занял {exec_time:.3f}s, статус: {status}")


async def auth(data: AuthRequest) -> Dict:
    """Авторизация пользователя"""

    try:
        logger.info(f"Попытка авторизации: {data}")

        login = await find_login_by_session(data)
        logger.debug(f"Логин: {login}")
        session_limit = 2
        ret = {}
        nasportid = nasportid_parse(data.NAS_Port_Id)

        # Договор найден, авторизуем
        if login and login.auth_type != "VIDEO":
            sessions = await find_sessions_by_login(login.login or "")
            session_count = len(sessions)
            logger.debug(f"Сессии найдены: {session_count}")

            timeto = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "timeto",
                None,
            )
            speed = getattr(
                getattr(getattr(login, "servicecats", None), "internet", None),
                "speed",
                None,
            )

            # Проверяем текущее время для сравнения со сроком действия услуги
            now_timestamp = datetime.now(tz=timezone.utc).timestamp()
            service_should_be_blocked = True

            if timeto is not None:
                try:
                    timeto_float = float(timeto)
                    service_should_be_blocked = timeto_float < now_timestamp
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid timeto value: {timeto}, error: {e}")

            # Выставляем услугу
            if not service_should_be_blocked:
                calc_speed = int(float(speed) * 1100) if speed is not None else 0
                ret.update(
                    {"reply:ERX-Service-Activate:1": f"INET-FREEDOM({calc_speed}k)"}
                )
            else:
                ret.update({"reply:ERX-Service-Activate:1": "NOINET-NOMONEY()"})

            # Реальник
            if login.ip_addr:
                ret.update({"reply:Framed-IP-Address": login.ip_addr})
            # Серые пулы
            else:
                ret.update({"reply:Framed-Pool": "pool-" + nasportid["psiface"]})

            # IPv6 только одна сессия и активная
            if (
                (getattr(login, "ipv6", None))
                and not service_should_be_blocked
                and session_count == 0
            ):
                ret.update(
                    {
                        "reply:Framed-IPv6-Prefix": login.ipv6,
                        "reply:Delegated-IPv6-Prefix": getattr(login, "ipv6_pd", ""),
                    }
                )

            ret.update({"reply:ERX-Virtual-Router-Name": "bng"})

            if login.login == "znvpn7132":
                ret.update({"reply:Framed-Route": "80.244.41.248/29"})

            if login.auth_type == "STATIC":
                ret.update({"reply:Idle-Timeout": "10"})

            ret.update(
                {
                    "reply:NAS-Port-Id": (data.User_Name or "")
                    + " | "
                    + (login.login or "")
                    + " | "
                    + (data.ADSL_Agent_Remote_Id or "")
                }
            )

            # PPPoE
            if data.Framed_Protocol == "PPP":
                ret.update(
                    {
                        "control:Cleartext-Password": {
                            "value": getattr(login, "password", "")
                        }
                    }
                )
            # IPOE во всех вариантах
            else:
                ret.update(
                    {
                        "control:Cleartext-Password": {"value": "ipoe"},
                        "control:Auth-Type": {"value": "Accept"},
                    }
                )

            ret.update(
                {
                    "reply:Reply-Message": {
                        "value": "Session type is " + (login.auth_type or "")
                    }
                }
            )

            # Лимит сессий (нужно думать внимательн)
            if session_count >= session_limit:
                ret.update(
                    {
                        "reply:Reply-Message": {
                            "value": "Session count over limit:"
                            + str(session_count)
                            + " login:"
                            + (login.login or "")
                        },
                        "control:Auth-Type": {"value": "Reject"},
                    }
                )
            # Дублирующая сессия, уже установленная на другом брасе, нужно разрешать
            if data.Framed_IP_Address:
                if getattr(login, "ipv6", None):
                    ret.update(
                        {
                            "reply:Framed-IPv6-Prefix": login.ipv6,
                            "reply:Delegated-IPv6-Prefix": getattr(
                                login, "ipv6_pd", ""
                            ),
                        }
                    )
                ret.update(
                    {
                        "reply:Reply-Message": {
                            "value": "Session is duplicated, type "
                            + (login.auth_type or "")
                        },
                        "control:Auth-Type": {"value": "Accept"},
                    }
                )
            # Реальник и есть другая сессия
            if session_count > 0 and login.ip_addr:
                ret.update(
                    {
                        "reply:Reply-Message": {
                            "value": "Static IP limit:"
                            + str(login.ip_addr)
                            + " login:"
                            + (login.login or "")
                        },
                        "control:Auth-Type": {"value": "Reject"},
                    }
                )

        # Видеокамеры
        elif login and login.auth_type == "VIDEO":
            ret.update(
                {
                    "reply:Framed-IP-Address": getattr(login, "ipAddress", ""),
                    "reply:ERX-Service-Activate:1": "INET-VIDEO()",
                    "reply:ERX-Virtual-Router-Name": "video",
                    "reply:NAS-Port-Id": (data.User_Name or "")
                    + " | "
                    + (login.login or "")
                    + " | "
                    + (data.ADSL_Agent_Remote_Id or ""),
                    "reply:Reply-Message": {
                        "value": "Session type is " + (login.auth_type or "")
                    },
                    "control:Auth-Type": {"value": "Accept"},
                }
            )
        # Договор не найден, сессия не авторизована
        else:
            ret.update(
                {
                    "reply:Reply-Message": "Session is unauth, login not found",
                    "reply:Framed-Pool": "novlan",
                    "reply:ERX-Virtual-Router-Name": "bng",
                    "reply:ERX-Service-Activate:1": "NOVLAN()",
                    "control:Auth-Type": "Accept",
                }
            )

        return ret
    except HTTPException as http_exc:
        logger.error(
            f"HTTP error processing authorization request: {http_exc}", exc_info=True
        )
        raise
    except Exception as e:
        logger.error(f"Error processing authorization request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


async def process_coa(login: str): ...
