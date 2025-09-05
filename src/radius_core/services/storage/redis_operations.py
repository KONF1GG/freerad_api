"""Redis операции для хранения и получения данных."""

import json
import logging
from typing import Optional
from pydantic import ValidationError

from ...config.settings import RADIUS_LOGIN_PREFIX

from ...models import SessionData
from ...clients import execute_redis_command, execute_redis_pipeline
from ...core.metrics import track_function

logger = logging.getLogger(__name__)


@track_function("redis", "get_session")
async def get_session_from_redis(redis_key: str, redis) -> Optional[SessionData]:
    """
    Получение сессии из Redis и преобразование в модель RedisSessionData.
    """
    try:
        session_data = await execute_redis_command(redis, "JSON.GET", redis_key)
        if not session_data:
            return None

        session = SessionData(**session_data)
        return session

    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON for key %s: %s", redis_key, e)
        return None
    except ValidationError as e:
        logger.error("Invalid session data for key %s: %s", redis_key, e)
        return None
    except Exception as e:
        logger.error("Failed to get session from Redis for key %s: %s", redis_key, e)
        return None


@track_function("redis", "save_session")
async def save_session_to_redis(
    session_data: SessionData, redis_key: str, redis
) -> bool:
    """Сохранение сессии в Redis с оптимизацией через pipeline"""
    try:
        # Используем pipeline для выполнения двух команд за один раз
        commands = [
            ("JSON.SET", redis_key, "$", session_data.model_dump_json(by_alias=True)),
            ("EXPIRE", redis_key, 1800),  # TTL на 30 минут
        ]
        await execute_redis_pipeline(commands, redis_conn=redis)
        return True
    except Exception as e:
        logger.error("Failed to save session to RedisJSON: %s", e)
        return False


@track_function("redis", "delete_session")
async def delete_session_from_redis(redis_key: str, redis) -> bool:
    """Удаление сессии из Redis"""
    try:
        result = await execute_redis_command(redis, "DEL", redis_key)
        return result > 0
    except Exception as e:
        logger.error("Failed to delete session from Redis: %s", e)
        return False


@track_function("redis", "check_login_exists")
async def _check_login_exists_in_redis(login: str, redis) -> bool:
    """Проверяет существование логина в Redis по ключу login:login"""
    try:
        login_key = f"{RADIUS_LOGIN_PREFIX}{login}"
        result = await execute_redis_command(redis, "EXISTS", login_key)
        return bool(result)
    except Exception as e:
        logger.error("Ошибка при проверке существования логина %s: %s", login, e)
        return False
