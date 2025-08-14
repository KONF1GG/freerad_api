import asyncio
from crud import find_sessions_by_login


async def main():
    sessions = await find_sessions_by_login("bnvpn4235")
    print(f"Found {len(sessions)} sessions:")
    for i, session in enumerate(sessions):
        print(
            f"Session {i + 1}: {session.Acct_Session_Id} - {session.Acct_Status_Type} - {session.ERX_Service_Session}"
        )


asyncio.run(main())
