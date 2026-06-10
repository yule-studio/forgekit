"""Shared agent contracts for the Yule Studio agent platform.

This package holds the *wire/interop contracts* that ``apps/*`` and other
``packages/*`` use to talk to each other and to the Agent Town front-end:

- :class:`AgentMessage` (+ :class:`RequestedAction`, :class:`Priority`,
  :class:`ContextRef`) — the inter-member message protocol.
- :class:`AgentRole` — structured ``<agent>/<role>`` identity.
- :class:`TaskRef` — a pointer to a task / issue / work item.
- :class:`AgentCommand` — an instruction directed at an agent.
- :class:`AgentEvent` — an event emitted by an agent.
- :class:`AgentStatus` — coarse lifecycle state.

Dependency rule: this package depends only on the standard library. It MUST
NOT import ``yule_engineering`` (the app) or any ``apps/*`` code — the arrow
points the other way (app → contracts).
"""

from __future__ import annotations

from .messages import (
    AgentMessage,
    ContextRef,
    Priority,
    REPLY_ACTIONS,
    REQUEST_ACTIONS,
    RequestedAction,
    TERMINAL_REPLY_ACTIONS,
    close_thread,
    new_request,
    reply_to,
    role_address,
    with_thread_id,
)
from .roles import (
    AgentRole,
    ENGINEERING_AGENT,
    GATEWAY,
    PLANNING_AGENT,
)
from .tasks import (
    KIND_BRIEF,
    KIND_ISSUE,
    KIND_PR,
    KIND_SESSION,
    KIND_TASK,
    TaskRef,
)
from .commands import AgentCommand
from .events import (
    AgentEvent,
    EVENT_APPROVAL_REQUESTED,
    EVENT_BLOCKED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PROGRESS,
    EVENT_STARTED,
    EVENT_STATE_CHANGED,
)
from .status import (
    AgentStatus,
    STALLED_STATUSES,
    TERMINAL_STATUSES,
)

__all__ = [
    # messages
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
    # roles
    "AgentRole",
    "ENGINEERING_AGENT",
    "PLANNING_AGENT",
    "GATEWAY",
    # tasks
    "TaskRef",
    "KIND_TASK",
    "KIND_ISSUE",
    "KIND_PR",
    "KIND_SESSION",
    "KIND_BRIEF",
    # commands
    "AgentCommand",
    # events
    "AgentEvent",
    "EVENT_STARTED",
    "EVENT_PROGRESS",
    "EVENT_COMPLETED",
    "EVENT_BLOCKED",
    "EVENT_FAILED",
    "EVENT_STATE_CHANGED",
    "EVENT_APPROVAL_REQUESTED",
    # status
    "AgentStatus",
    "STALLED_STATUSES",
    "TERMINAL_STATUSES",
]
