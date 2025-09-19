"""Модуль Change of Authorization (CoA)."""

from .coa_operations import (
    send_coa_to_queue,
    send_coa_session_kill,
    send_coa_session_set,
)

__all__ = [
    "send_coa_to_queue",
    "send_coa_session_kill",
    "send_coa_session_set",
]
