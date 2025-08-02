from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from datetime import datetime


class AccountingData(BaseModel):
    User_Name: Optional[str] = Field(None, alias="User-Name")
    Acct_Status_Type: str = Field(
        ..., alias="Acct-Status-Type", description="Start, Interim-Update, Stop"
    )
    Acct_Session_Id: str = Field(..., alias="Acct-Session-Id")
    Event_Timestamp: str = Field(..., alias="Event-Timestamp")
    Acct_Session_Time: Optional[int] = Field(None, alias="Acct-Session-Time")
    Acct_Delay_Time: Optional[int] = Field(None, alias="Acct-Delay-Time")
    Service_Type: Optional[str] = Field(None, alias="Service-Type")
    Filter_Id: Optional[str] = Field(None, alias="Filter-Id")
    ERX_Cos_Shaping_Rate: Optional[str] = Field(None, alias="ERX-Cos-Shaping-Rate")
    Framed_IPv6_Prefix: Optional[str] = Field(None, alias="Framed-IPv6-Prefix")
    Delegated_IPv6_Prefix: Optional[str] = Field(None, alias="Delegated-IPv6-Prefix")
    Framed_IPv6_Pool: Optional[str] = Field(None, alias="Framed-IPv6-Pool")
    Acct_Authentic: Optional[str] = Field(None, alias="Acct-Authentic")
    Calling_Station_Id: Optional[str] = Field(None, alias="Calling-Station-Id")
    ERX_Dhcp_Options: Optional[str] = Field(None, alias="ERX-Dhcp-Options")
    ERX_Dhcp_Mac_Addr: Optional[str] = Field(None, alias="ERX-Dhcp-Mac-Addr")
    Framed_IP_Address: Optional[str] = Field(None, alias="Framed-IP-Address")
    Framed_IP_Netmask: Optional[str] = Field(None, alias="Framed-IP-Netmask")
    ERX_Service_Session: Optional[str] = Field(None, alias="ERX-Service-Session")
    NAS_Identifier: Optional[str] = Field(None, alias="NAS-Identifier")
    NAS_Port: Optional[str] = Field(None, alias="NAS-Port")
    NAS_Port_Id: Optional[str] = Field(None, alias="NAS-Port-Id")
    NAS_Port_Type: Optional[str] = Field(None, alias="NAS-Port-Type")
    ERX_Virtual_Router_Name: Optional[str] = Field(
        None, alias="ERX-Virtual-Router-Name"
    )
    ERX_Pppoe_Description: Optional[str] = Field(None, alias="ERX-Pppoe-Description")
    ADSL_Agent_Circuit_Id: Optional[str] = Field(None, alias="ADSL-Agent-Circuit-Id")
    ADSL_Agent_Remote_Id: Optional[str] = Field(None, alias="ADSL-Agent-Remote-Id")
    ERX_DHCP_First_Relay_IPv4_Address: Optional[str] = Field(
        None, alias="ERX-DHCP-First-Relay-IPv4-Address"
    )
    NAS_IP_Address: Optional[str] = Field(None, alias="NAS-IP-Address")
    Acct_Unique_Session_Id: str = Field(..., alias="Acct-Unique-Session-Id")
    Acct_Input_Octets: Optional[int] = Field(None, alias="Acct-Input-Octets")
    Acct_Output_Octets: Optional[int] = Field(None, alias="Acct-Output-Octets")
    Acct_Input_Gigawords: Optional[int] = Field(None, alias="Acct-Input-Gigawords")
    Acct_Output_Gigawords: Optional[int] = Field(None, alias="Acct-Output-Gigawords")
    Acct_Input_Packets: Optional[int] = Field(None, alias="Acct-Input-Packets")
    Acct_Output_Packets: Optional[int] = Field(None, alias="Acct-Output-Packets")
    Acct_Terminate_Cause: Optional[str] = Field(None, alias="Acct-Terminate-Cause")

    class Config:
        extra = "allow"
        allow_population_by_field_name = True


class AccountingResponse(BaseModel):
    action: Literal["noop", "kill", "update", "log"] = "noop"
    reason: Optional[str] = None
    coa_attributes: Optional[Dict[str, Any]] = None
    status: Literal["success", "error"] = "success"
    session_id: Optional[str] = None


class LoginData(BaseModel):
    login: str
    contract: Optional[str] = None
    auth_type: Optional[str] = None
    mac: Optional[str] = None
    vlan: Optional[str] = None
    onu_mac: Optional[str] = None
    ip_addr: Optional[str] = None
    servicecats: Optional[Dict[str, Any]] = None


class SessionData(BaseModel):
    session_id: str
    login: Optional[str] = None
    auth_type: str = "UNKNOWN"
    contract: Optional[str] = None
    onu_mac: Optional[str] = None
    service: Optional[str] = None
    start_time: Optional[datetime] = None
    session_time: int = 0
    input_octets: int = 0
    output_octets: int = 0
    input_gigawords: int = 0
    output_gigawords: int = 0
    nas_identifier: Optional[str] = None
    nas_port_id: Optional[str] = None
    framed_ip: Optional[str] = None
    calling_station_id: Optional[str] = None
    terminate_cause: Optional[str] = None
    is_active: bool = True

    class Config:
        extra = "allow"


class TrafficData(BaseModel):
    session_id: str
    login: Optional[str] = None
    timestamp: datetime
    input_octets: int = 0
    output_octets: int = 0
    input_gigawords: int = 0
    output_gigawords: int = 0
    session_time: int = 0

    class Config:
        extra = "allow"
