import asyncio
import sys
import os

# Добавляем src в путь для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from radius_core.services import find_sessions_by_login
from radius_core.clients import get_redis


async def main():
    redis_client = await get_redis()
    sessions = await find_sessions_by_login("bnvpn4235", redis=redis_client)
    print(f"Found {len(sessions)} sessions:")
    for i, session in enumerate(sessions):
        print(
            f"Session {i + 1}: {session.Acct_Session_Id} - {session.Acct_Status_Type} - {session.ERX_Service_Session}"
        )


if __name__ == "__main__":
    asyncio.run(main())
