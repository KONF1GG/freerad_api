"""Обогащение данных сессии информацией о логине."""

import logging
from typing import Optional
from pydantic import ValidationError

from ...models import AccountingData, EnrichedSessionData, LoginSearchResult

logger = logging.getLogger(__name__)


async def enrich_session_with_login(
    session_req: AccountingData, login: Optional[LoginSearchResult]
) -> EnrichedSessionData:
    """
    Обогащение данных сессии информацией о логине.

    Args:
        session_req: Данные сессии (AccountingData).
        login: Данные логина (LoginSearchResult) или None.

    Returns:
        EnrichedSessionData: Обогащенная модель сессии с данными логина.
    """
    session_dict = session_req.model_dump(by_alias=True)

    if login:
        session_dict.update(login.model_dump(by_alias=True))

    # if session_dict.get("ERX-Service-Session"):
    #     session_dict["service"] = session_dict["ERX-Service-Session"]

    try:
        return EnrichedSessionData(**session_dict)
    except ValidationError as e:
        logger.error("Failed to create EnrichedSessionData: %s", e)
        accounting_fields = AccountingData.model_fields.keys()
        return EnrichedSessionData(
            **{k: v for k, v in session_dict.items() if k in accounting_fields}
        )
