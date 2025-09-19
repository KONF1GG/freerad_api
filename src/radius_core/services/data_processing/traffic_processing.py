"""Обработка данных трафика для сессии."""

from ...models import EnrichedSessionData


def process_traffic_data(session_req: EnrichedSessionData) -> EnrichedSessionData:
    """Преобразование данных трафика для сессии."""
    if session_req.Acct_Status_Type == "Start":
        return session_req

    session_req.Acct_Input_Octets = (
        session_req.Acct_Input_Gigawords << 32
    ) | session_req.Acct_Input_Octets
    session_req.Acct_Output_Octets = (
        session_req.Acct_Output_Gigawords << 32
    ) | session_req.Acct_Output_Octets
    session_req.Acct_Input_Packets = (
        session_req.ERX_Input_Gigapkts << 32
    ) | session_req.Acct_Input_Packets
    session_req.Acct_Output_Packets = (
        session_req.ERX_Output_Gigapkts << 32
    ) | session_req.Acct_Output_Packets
    session_req.ERX_IPv6_Acct_Input_Octets = (
        session_req.ERX_IPv6_Acct_Input_Gigawords << 32
    ) | session_req.ERX_IPv6_Acct_Input_Octets
    session_req.ERX_IPv6_Acct_Output_Octets = (
        session_req.ERX_IPv6_Acct_Output_Gigawords << 32
    ) | session_req.ERX_IPv6_Acct_Output_Octets

    return session_req
