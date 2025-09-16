"""
Модуль для подготовки данных сессии
"""
from typing import Tuple, Union

from ..models import AccountingData, AuthRequest
from .helpers import is_username_mac, mac_from_username, mac_from_hex, nasportid_parse

async def get_username_onu_mac_vlan_from_data(
    data: Union[AccountingData, AuthRequest],
) -> Tuple[str, str, str, bool]:
    """Извлекает username, onu_mac и vlan из данных сессии"""
    username = ""
    onu_mac = ""
    vlan = ""
    is_mac_username = False

    if data.User_Name:
        is_mac_username = is_username_mac(data.User_Name)
        if is_mac_username:
            username = mac_from_username(data.User_Name)
        else:
            username = data.User_Name

    if data.ADSL_Agent_Remote_Id:
        onu_mac = mac_from_hex(data.ADSL_Agent_Remote_Id)

    if data.NAS_Port_Id:
        nasportid = nasportid_parse(data.NAS_Port_Id)
        vlan = nasportid.get("cvlan") or nasportid.get("svlan", "")

    return username, onu_mac, vlan, is_mac_username
