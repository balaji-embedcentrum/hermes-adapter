"""A2A (Agent-to-Agent) adapter — server + client for hermes-agent."""

from .client import (
    a2a_call,
    a2a_discover,
    a2a_local_scan,
    register_hermes_tools,
    A2A_CALL_SCHEMA,
    A2A_DISCOVER_SCHEMA,
    A2A_LOCAL_SCAN_SCHEMA,
)

__all__ = [
    "a2a_call",
    "a2a_discover",
    "a2a_local_scan",
    "register_hermes_tools",
    "A2A_CALL_SCHEMA",
    "A2A_DISCOVER_SCHEMA",
    "A2A_LOCAL_SCAN_SCHEMA",
]
