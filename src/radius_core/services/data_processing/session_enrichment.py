"""Обогащение данных сессии информацией о логине."""

import logging
from typing import Optional, Union
from pydantic import ValidationError

from ...models.schemas import VideoLoginSearchResult

from ...models import AccountingData, EnrichedSessionData, LoginSearchResult

logger = logging.getLogger(__name__)


async def enrich_session_with_login(
    session_req: AccountingData,
    login: Optional[Union[LoginSearchResult, VideoLoginSearchResult]],
) -> EnrichedSessionData:
    """
    Обогащение данных сессии информацией о логине.

    Args:
        session_req: Данные сессии (AccountingData).
        login: Данные логина (LoginSearchResult,  VideoLoginSearchResult) или None.

    Returns:
        EnrichedSessionData: Обогащенная модель сессии с данными логина.
    """
    session_dict = session_req.model_dump(by_alias=True)
    session_dict.update(login.model_dump(by_alias=True))

    return EnrichedSessionData(**session_dict)
