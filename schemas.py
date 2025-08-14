from pydantic import BaseModel, Field, ValidationInfo, field_validator, ConfigDict
from typing import Optional, Dict, Any, Literal, Union
from utils import parse_event
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class BaseAccountingData(BaseModel):
    Acct_Session_Id: str = Field(..., alias="Acct-Session-Id")
    Acct_Status_Type: str = Field("UNKNOWN", alias="Acct-Status-Type")
    Event_Timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc), alias="Event-Timestamp"
    )
    Acct_Unique_Session_Id: str = Field(..., alias="Acct-Unique-Session-Id")

    User_Name: Optional[str] = Field(None, alias="User-Name")
    Acct_Session_Time: int = Field(0, alias="Acct-Session-Time")
    Acct_Delay_Time: Optional[int] = Field(0, alias="Acct-Delay-Time")
    Acct_Input_Octets: int = Field(0, alias="Acct-Input-Octets")
    Acct_Output_Octets: int = Field(0, alias="Acct-Output-Octets")
    Acct_Input_Packets: int = Field(0, alias="Acct-Input-Packets")
    Acct_Output_Packets: int = Field(0, alias="Acct-Output-Packets")
    Acct_Input_Gigawords: int = Field(0, alias="Acct-Input-Gigawords")
    Acct_Output_Gigawords: int = Field(0, alias="Acct-Output-Gigawords")
    ERX_Input_Gigapkts: int = Field(0, alias="ERX-Input-Gigapkts")
    ERX_Output_Gigapkts: int = Field(0, alias="ERX-Output-Gigapkts")
    Framed_IP_Address: Optional[str] = Field(None, alias="Framed-IP-Address")
    Framed_IP_Netmask: Optional[str] = Field(None, alias="Framed-IP-Netmask")
    Framed_IPv6_Prefix: Optional[str] = Field(None, alias="Framed-IPv6-Prefix")
    Delegated_IPv6_Prefix: Optional[str] = Field(None, alias="Delegated-IPv6-Prefix")
    Framed_IPv6_Pool: Optional[str] = Field(None, alias="Framed-IPv6-Pool")
    ERX_IPv6_Acct_Input_Octets: int = Field(0, alias="ERX-IPv6-Acct-Input-Octets")
    ERX_IPv6_Acct_Output_Octets: int = Field(0, alias="ERX-IPv6-Acct-Output-Octets")
    ERX_IPv6_Acct_Input_Packets: int = Field(0, alias="ERX-IPv6-Acct-Input-Packets")
    ERX_IPv6_Acct_Output_Packets: int = Field(0, alias="ERX-IPv6-Acct-Output-Packets")
    ERX_IPv6_Acct_Input_Gigawords: int = Field(0, alias="ERX-IPv6-Acct-Input-Gigawords")
    ERX_IPv6_Acct_Output_Gigawords: int = Field(
        0, alias="ERX-IPv6-Acct-Output-Gigawords"
    )
    Calling_Station_Id: Optional[str] = Field(None, alias="Calling-Station-Id")
    ERX_Dhcp_Options: Optional[str] = Field(None, alias="ERX-Dhcp-Options")
    ERX_Dhcp_Mac_Addr: Optional[str] = Field(None, alias="ERX-Dhcp-Mac-Addr")
    ERX_Service_Session: Optional[str] = Field(None, alias="ERX-Service-Session")
    ERX_Pppoe_Description: Optional[str] = Field(None, alias="ERX-Pppoe-Description")
    ERX_DHCP_First_Relay_IPv4_Address: Optional[str] = Field(
        None, alias="ERX-DHCP-First-Relay-IPv4-Address"
    )
    NAS_Identifier: Optional[str] = Field(None, alias="NAS-Identifier")
    NAS_IP_Address: Optional[str] = Field(None, alias="NAS-IP-Address")
    NAS_Port: Optional[str] = Field(None, alias="NAS-Port")
    NAS_Port_Id: Optional[str] = Field(None, alias="NAS-Port-Id")
    NAS_Port_Type: Optional[str] = Field(None, alias="NAS-Port-Type")
    Service_Type: Optional[str] = Field(None, alias="Service-Type")
    Filter_Id: Optional[str] = Field(None, alias="Filter-Id")
    ERX_Cos_Shaping_Rate: Optional[str] = Field(None, alias="ERX-Cos-Shaping-Rate")
    Acct_Authentic: Optional[str] = Field(None, alias="Acct-Authentic")
    ERX_Virtual_Router_Name: Optional[str] = Field(
        None, alias="ERX-Virtual-Router-Name"
    )
    ADSL_Agent_Circuit_Id: Optional[str] = Field(None, alias="ADSL-Agent-Circuit-Id")
    ADSL_Agent_Remote_Id: Optional[str] = Field(None, alias="ADSL-Agent-Remote-Id")
    ERX_Acct_Request_Reason: Optional[str] = Field(
        None, alias="ERX-Acct-Request-Reason"
    )
    Acct_Terminate_Cause: Optional[str] = Field(None, alias="Acct-Terminate-Cause")

    @field_validator("Event_Timestamp", mode="before")
    def parse_timestamp(cls, ts: str | datetime | dict) -> datetime:
        """Normalize various timestamp inputs to a timezone-aware UTC datetime."""
        return parse_event(ts)

    @field_validator(
        "Acct_Session_Time",
        "Acct_Delay_Time",
        "Acct_Input_Gigawords",
        "Acct_Output_Gigawords",
        "ERX_Input_Gigapkts",
        "ERX_Output_Gigapkts",
        "ERX_IPv6_Acct_Input_Packets",
        "ERX_IPv6_Acct_Output_Packets",
        "ERX_IPv6_Acct_Input_Gigawords",
        "ERX_IPv6_Acct_Output_Gigawords",
        "Acct_Input_Octets",
        "Acct_Output_Octets",
        "Acct_Input_Packets",
        "Acct_Output_Packets",
        "ERX_IPv6_Acct_Input_Octets",
        "ERX_IPv6_Acct_Output_Octets",
        mode="before",
    )
    def safe_int(cls, value: Any, info: ValidationInfo) -> int:
        """Безопасное преобразование в int из любого типа."""
        # Если None или пустая строка — 0
        if value is None or value == "":
            return 0

        # Если dict с ключом 'value' — берём первый элемент
        if isinstance(value, dict):
            value = value.get("value", [0])[0]

        # Если строка — пробуем int()
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return 0
            try:
                return int(value)
            except ValueError:
                logger.warning(
                    f"Invalid string for {info.field_name}: '{value}', using 0"
                )
                return 0

        # Если число — сразу int()
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(
                f"Unsupported type for {info.field_name}: {type(value)} ({value}), using 0"
            )
            return 0

    model_config = ConfigDict(populate_by_name=True)


class AccountingData(BaseAccountingData):
    pass


class AccountingResponse(BaseModel):
    action: Literal["noop", "kill", "update", "log"] = "noop"
    reason: Optional[str] = None
    coa_attributes: Optional[Dict[str, Any]] = None
    status: Literal["success", "error"] = "success"
    session_id: Optional[str] = None


class ServiceCategory(BaseModel):
    """Категория сервиса."""

    timeto: Optional[int] = None
    speed: Optional[int] = None


class ServiceCats(BaseModel):
    """Категории сервисов."""

    internet: Optional[ServiceCategory] = None


class LoginBase(BaseModel):
    """Базовая модель для данных логина."""

    login: Optional[str] = Field(default="", description="Логин пользователя")
    auth_type: Optional[str] = Field(default="UNAUTH", description="Тип аутентификации")
    contract: Optional[str] = Field(default="", description="Контракт пользователя")
    onu_mac: Optional[str] = Field(default="", description="MAC-адрес ONU")
    #    vlan: Optional[str] = Field(default="", description="VLAN")
    ip_addr: Optional[str] = Field(
        default=None, description="IP-адрес из данных логина"
    )
    servicecats: Optional[ServiceCats] = None

    ipv6: Optional[str] = None
    ipv6_pd: Optional[str] = None
    password: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class SessionData(BaseAccountingData, LoginBase):
    GMT: Optional[int] = 5
    Acct_Start_Time: Optional[datetime] = Field(None, alias="Acct-Start-Time")
    Acct_Stop_Time: Optional[datetime] = Field(None, alias="Acct-Stop-Time")
    Acct_Update_Time: Optional[datetime] = Field(None, alias="Acct-Update-Time")

    model_config = ConfigDict(populate_by_name=True)


class LoginSearchResult(LoginBase):
    """Модель для результата поиска логина."""

    pass


class EnrichedSessionData(AccountingData, LoginBase):
    """Модель для сессии с данными логина."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class TrafficData(BaseModel):
    """Модель для данных трафика."""

    Acct_Unique_Session_Id: str = Field(..., alias="Acct-Unique-Session-Id")
    login: str
    timestamp: str

    # IPv4 трафик
    Acct_Input_Octets: int = Field(0, alias="Acct-Input-Octets")
    Acct_Output_Octets: int = Field(0, alias="Acct-Output-Octets")
    Acct_Input_Packets: int = Field(0, alias="Acct-Input-Packets")
    Acct_Output_Packets: int = Field(0, alias="Acct-Output-Packets")

    # IPv6 трафик
    ERX_IPv6_Acct_Input_Octets: int = Field(0, alias="ERX-IPv6-Acct-Input-Octets")
    ERX_IPv6_Acct_Output_Octets: int = Field(0, alias="ERX-IPv6-Acct-Output-Octets")
    ERX_IPv6_Acct_Input_Packets: int = Field(0, alias="ERX-IPv6-Acct-Input-Packets")
    ERX_IPv6_Acct_Output_Packets: int = Field(0, alias="ERX-IPv6-Acct-Output-Packets")

    @field_validator(
        "Acct_Input_Octets",
        "Acct_Output_Octets",
        "Acct_Input_Packets",
        "Acct_Output_Packets",
        "ERX_IPv6_Acct_Input_Octets",
        "ERX_IPv6_Acct_Output_Octets",
        "ERX_IPv6_Acct_Input_Packets",
        "ERX_IPv6_Acct_Output_Packets",
        mode="before",
    )
    def ensure_non_negative(cls, value):
        """Гарантирует, что значения трафика не отрицательные."""
        if value is None:
            return 0
        return max(0, int(value))

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class CoaRequest(BaseModel):
    """Модель запроса CoA"""

    login: str


class AuthRequest(BaseModel):
    """Модель запроса авторизации RADIUS (Access-Request)"""

    Packet_Type: str = Field(..., alias="Packet-Type")
    User_Name: str = Field(..., alias="User-Name")
    Service_Type: str = Field(..., alias="Service-Type")

    Acct_Session_Id: str = Field(..., alias="Acct-Session-Id")
    Framed_Protocol: str = Field(..., alias="Framed-Protocol")

    CHAP_Password: Optional[str] = Field(None, alias="CHAP-Password")
    CHAP_Challenge: Optional[str] = Field(None, alias="CHAP-Challenge")

    Calling_Station_Id: str = Field(..., alias="Calling-Station-Id")
    ERX_Dhcp_Mac_Addr: str = Field(..., alias="ERX-Dhcp-Mac-Addr")
    ERX_Dhcp_Options: str = Field(..., alias="ERX-Dhcp-Options")

    NAS_Identifier: str = Field(..., alias="NAS-Identifier")
    NAS_IP_Address: str = Field(..., alias="NAS-IP-Address")
    NAS_Port: str = Field(..., alias="NAS-Port")
    NAS_Port_Id: str = Field(..., alias="NAS-Port-Id")
    NAS_Port_Type: str = Field(..., alias="NAS-Port-Type")

    Framed_IP_Address: Optional[str] = Field(None, alias="Framed-IP-Address")
    Framed_IP_Netmask: Optional[str] = Field(None, alias="Framed-IP-Netmask")

    ERX_Virtual_Router_Name: str = Field(..., alias="ERX-Virtual-Router-Name")
    ERX_Pppoe_Description: str = Field(..., alias="ERX-Pppoe-Description")
    ADSL_Agent_Circuit_Id: str = Field(..., alias="ADSL-Agent-Circuit-Id")
    ADSL_Agent_Remote_Id: str = Field(..., alias="ADSL-Agent-Remote-Id")
    ERX_DHCP_First_Relay_IPv4_Address: str = Field(
        ..., alias="ERX-DHCP-First-Relay-IPv4-Address"
    )

    Event_Timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc), alias="Event-Timestamp"
    )

    @field_validator("Event_Timestamp", mode="before")
    def parse_timestamp(cls, ts: str | datetime | dict) -> datetime:
        """Normalize various timestamp inputs to a timezone-aware UTC datetime."""
        return parse_event(ts)

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AuthDataLog(BaseModel):
    """Модель данных авторизации для логирования"""

    username: Optional[str] = None
    password: Optional[str] = None
    callingstationid: Optional[str] = None
    nasipaddress: Optional[str] = None
    reply: Optional[str] = None  # Access-Accept / Access-Reject
    reason: Optional[str] = None  # текст из Reply-Message
    speed: Optional[float] = None  # исходная скорость услуги
    pool: Optional[str] = None
    agentremoteid: Optional[str] = None
    agentcircuitid: Optional[str] = None
    authdate: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuthResponse(BaseModel):
    # Reply
    reply_framed_ip_address: Optional[str] = Field(
        None, alias="reply:Framed-IP-Address"
    )
    reply_framed_pool: Optional[str] = Field(None, alias="reply:Framed-Pool")
    reply_framed_ipv6_prefix: Optional[str] = Field(
        None, alias="reply:Framed-IPv6-Prefix"
    )
    reply_delegated_ipv6_prefix: Optional[str] = Field(
        None, alias="reply:Delegated-IPv6-Prefix"
    )
    reply_framed_route: Optional[str] = Field(None, alias="reply:Framed-Route")
    reply_erx_service_activate: Optional[str] = Field(
        None, alias="reply:ERX-Service-Activate:1"
    )
    reply_erx_virtual_router_name: Optional[str] = Field(
        None, alias="reply:ERX-Virtual-Router-Name"
    )
    reply_nas_port_id: Optional[str] = Field(None, alias="reply:NAS-Port-Id")
    reply_idle_timeout: Optional[str] = Field(None, alias="reply:Idle-Timeout")
    reply_message: Optional[Dict[str, str]] = Field(None, alias="reply:Reply-Message")

    # Control
    control_cleartext_password: Optional[Dict[str, str]] = Field(
        None, alias="control:Cleartext-Password"
    )
    control_auth_type: Optional[Dict[str, str]] = Field(None, alias="control:Auth-Type")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def to_radius(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


RABBIT_MODELS = TrafficData | SessionData | AuthDataLog
