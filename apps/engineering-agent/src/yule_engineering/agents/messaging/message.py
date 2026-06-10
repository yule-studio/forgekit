"""Compatibility shim — the agent message protocol moved to ``agent-contracts``.

The canonical home of this contract is now
``yule_agent_contracts.messages`` (the ``packages/agent-contracts`` package).
This module re-exports it unchanged so existing imports such as::

    from yule_engineering.agents.messaging.message import AgentMessage
    from yule_engineering.agents import AgentMessage  # via agents/__init__

keep working. New code should import from ``yule_agent_contracts`` directly.

Dependency direction: the app (``yule_engineering``) depends on the contracts
package, never the reverse.
"""

from __future__ import annotations

from yule_agent_contracts.messages import (
    REPLY_ACTIONS,
    REQUEST_ACTIONS,
    TERMINAL_REPLY_ACTIONS,
    AgentMessage,
    ContextRef,
    Priority,
    RequestedAction,
    close_thread,
    new_request,
    reply_to,
    role_address,
    with_thread_id,
)

__all__ = [
    "AgentMessage",
    "ContextRef",
    "Priority",
    "RequestedAction",
    "REQUEST_ACTIONS",
    "REPLY_ACTIONS",
    "TERMINAL_REPLY_ACTIONS",
    "new_request",
    "reply_to",
    "close_thread",
    "with_thread_id",
    "role_address",
]
