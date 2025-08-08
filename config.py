import os

# Redis Configuration
REDIS_URL: str = os.getenv("REDIS_URL", "")
REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "10"))
REDIS_CONCURRENCY: int = int(
    os.getenv("REDIS_CONCURRENCY", str(max(1, int(REDIS_POOL_SIZE * 0.9))))
)

REDIS_COMMAND_TIMEOUT: float = float(os.getenv("REDIS_COMMAND_TIMEOUT", "5.0"))

# RabbitMQ Configuration
AMQP_URL: str = os.getenv("AMQP_URL", "")
AMQP_EXCHANGE: str = os.getenv("AMQP_EXCHANGE", "sessions_traffic_exchange")
AMQP_SESSION_QUEUE: str = os.getenv("AMQP_SESSION_QUEUE", "session_queue")
AMQP_TRAFFIC_QUEUE: str = os.getenv("AMQP_TRAFFIC_QUEUE", "traffic_queue")

# Radius Configuration
RADIUS_SESSION_PREFIX: str = "radius:session:"
RADIUS_LOGIN_PREFIX: str = "login:"
RADIUS_INDEX_NAME: str = "idx:radius:login"

# Session Template
SESSION_TEMPLATE = {
    "login": "",
    "auth_type": "",
    "onu_mac": "",
    "contract": "",
    "service": "",
    "GMT": 5,
    "User_Name": "",
    "Acct_Status_Type": "",
    "Acct_Start_Time": "",
    "Acct_Session_Time": "",
    "Acct_Stop_Time": "",
    "Acct_Session_Id": "",
    "ERX_Service_Session": "",
    "Acct_Unique_Session_Id": "",
    "Acct_Terminate_Cause": "",
    "Event_Timestamp": "",
    "Acct_Input_Octets": 0,
    "Acct_Output_Octets": 0,
    "Acct_Input_Packets": 0,
    "Acct_Output_Packets": 0,
    "Acct_Input_Gigawords": 0,
    "Acct_Output_Gigawords": 0,
    "ERX_Input_Gigapkts": 0,
    "ERX_Output_Gigapkts": 0,
    "Acct_Delay_Time": "",
    "Service_Type": "",
    "Framed_Protocol": "",
    "ERX_Cos_Shaping_Rate": "",
    "Framed_IPv6_Prefix": "",
    "Delegated_IPv6_Prefix": "",
    "Framed_IPv6_Pool": "",
    "Acct_Authentic": "",
    "Calling_Station_Id": "",
    "ERX_Dhcp_Options": "",
    "ERX_Dhcp_Mac_Addr": "",
    "Framed_IP_Address": "",
    "Framed_IP_Netmask": "",
    "NAS_Identifier": "",
    "NAS_Port": "",
    "NAS_Port_Id": "",
    "NAS_Port_Type": "",
    "ERX_IPv6_Acct_Input_Octets": 0,
    "ERX_IPv6_Acct_Output_Octets": 0,
    "ERX_IPv6_Acct_Input_Packets": 0,
    "ERX_IPv6_Acct_Output_Packets": 0,
    "ERX_IPv6_Acct_Input_Gigawords": 0,
    "ERX_IPv6_Acct_Output_Gigawords": 0,
    "ERX_Virtual_Router_Name": "",
    "ERX_Pppoe_Description": "",
    "ADSL_Agent_Circuit_Id": "",
    "ADSL_Agent_Remote_Id": "",
    "ERX_DHCP_First_Relay_IPv4_Address": "",
    "ERX_Acct_Request_Reason": "",
    "NAS_IP_Address": "",
}
