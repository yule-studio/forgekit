"""Inter-member message protocol for engineering-agent (and future departments).

The schema is intentionally department-agnostic: ``from_role`` / ``to_role``
are free-form strings shaped as ``"<agent>/<role>"`` (e.g.
``"engineering-agent/tech-lead"``). When cto-agent, design-agent, and
marketing-agent come online they use the same dataclass without changes.

Round-trip pattern:

    tech-lead --new_request--> backend-engineer
                                   │
                                   └── reply_to(parent) ──> tech-lead
                                                              │
                                                              └── close_thread(parent_chain) ──▶ session

Persistence and transport (in-process queue / Discord thread / socket) are
out of scope for this module. Consumers are expected to wrap these
dataclasses into whichever channel they own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


class RequestedAction(str, Enum):
    """Verbs that say what the recipient is being asked to do (or did).

    Outgoing intent (tech-lead → role): ANALYZE / ADVISE / IMPLEMENT /
    REVIEW / TEST / DESIGN / INVESTIGATE / HANDOFF.
    Replies (role → tech-lead): COMPLETED / IN_PROGRESS / NEEDS_CLARIFICATION
    / BLOCKED / REJECTED.
    The schema does not enforce direction; helpers in this module produce
    well-formed pairs.
    """

    ANALYZE = "analyze"
    ADVISE = "advise"
    IMPLEMENT = "implement"
    REVIEW = "review"
    TEST = "test"
    DESIGN = "design"
    INVESTIGATE = "investigate"
    HANDOFF = "handoff"
    ACKNOWLEDGE = "acknowledge"

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    REJECTED = "rejected"


REQUEST_ACTIONS = frozenset(
    {
        RequestedAction.ANALYZE,
        RequestedAction.ADVISE,
        RequestedAction.IMPLEMENT,
        RequestedAction.REVIEW,
        RequestedAction.TEST,
        RequestedAction.DESIGN,
        RequestedAction.INVESTIGATE,
        RequestedAction.HANDOFF,
        RequestedAction.ACKNOWLEDGE,
    }
)

REPLY_ACTIONS = frozenset(
    {
        RequestedAction.IN_PROGRESS,
        RequestedAction.COMPLETED,
        RequestedAction.NEEDS_CLARIFICATION,
        RequestedAction.BLOCKED,
        RequestedAction.REJECTED,
    }
)

TERMINAL_REPLY_ACTIONS = frozenset(
    {RequestedAction.COMPLETED, RequestedAction.REJECTED}
)


class Priority(str, Enum):
    P0 = "P0"  # urgent / blocker
    P1 = "P1"  # high
    P2 = "P2"  # normal — default
    P3 = "P3"  # low


@dataclass(frozen=True)
class ContextRef:
    """Pointer to an external artifact (PR / issue / file / discord message)."""

    kind: str
    value: str
    label: Optional[str] = None


@dataclass(frozen=True)
class AgentMessage:
    """One message between two agent/role pairs.

    Fields fall into four groups:

    - **Routing**: ``from_role``, ``to_role``.
    - **Task framing**: ``task_type``, ``topic``, ``content``,
      ``requested_action``, ``priority``.
    - **Threading**: ``thread_id``, ``run_id``, ``parent_message_id``,
      ``message_id``, ``created_at``.
    - **Reference pack** (UI/UX/marketing/content work): ``context_refs``,
      ``reference_links``, ``reference_notes``, ``visual_direction``,
      ``copy_tone``, ``competitive_examples``.

    The pack fields use neutral types so cto-agent / design-agent /
    marketing-agent can reuse them without subclassing.
    """

    # routing
    from_role: str
    to_role: str

    # task framing
    task_type: str
    topic: str
    content: str
    requested_action: RequestedAction
    priority: Priority = Priority.P2

    # threading metadata
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=datetime.utcnow)

    # reference pack (optional, used when task touches UI/UX/marketing/content)
    context_refs: Sequence[ContextRef] = ()
    reference_links: Sequence[str] = ()
    reference_notes: Sequence[Mapping[str, Any]] = ()
    visual_direction: Optional[str] = None
    copy_tone: Optional[str] = None
    competitive_examples: Sequence[Mapping[str, Any]] = ()

    extra: Mapping[str, Any] = field(default_factory=dict)

    def is_request(self) -> bool:
        return self.requested_action in REQUEST_ACTIONS

    def is_reply(self) -> bool:
        return self.requested_action in REPLY_ACTIONS

    def is_terminal_reply(self) -> bool:
        return self.requested_action in TERMINAL_REPLY_ACTIONS


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def new_request(
    *,
    from_role: str,
    to_role: str,
    task_type: str,
    topic: str,
    content: str,
    requested_action: RequestedAction,
    priority: Priority = Priority.P2,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    context_refs: Sequence[ContextRef] = (),
    reference_links: Sequence[str] = (),
    reference_notes: Sequence[Mapping[str, Any]] = (),
    visual_direction: Optional[str] = None,
    copy_tone: Optional[str] = None,
    competitive_examples: Sequence[Mapping[str, Any]] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> AgentMessage:
    """Build a request message (e.g. tech-lead → backend-engineer)."""

    if requested_action not in REQUEST_ACTIONS:
        raise ValueError(
            f"new_request expects a request action; got {requested_action.value}. "
            f"Use reply_to for {requested_action.value}."
        )
    return AgentMessage(
        from_role=from_role,
        to_role=to_role,
        task_type=task_type,
        topic=topic,
        content=content,
        requested_action=requested_action,
        priority=priority,
        run_id=run_id,
        thread_id=thread_id,
        context_refs=tuple(context_refs),
        reference_links=tuple(reference_links),
        reference_notes=tuple(dict(item) for item in reference_notes),
        visual_direction=visual_direction,
        copy_tone=copy_tone,
        competitive_examples=tuple(dict(item) for item in competitive_examples),
        extra=dict(extra or {}),
    )


def reply_to(
    parent: AgentMessage,
    *,
    content: str,
    requested_action: RequestedAction,
    priority: Optional[Priority] = None,
    context_refs: Sequence[ContextRef] = (),
    reference_links: Sequence[str] = (),
    reference_notes: Sequence[Mapping[str, Any]] = (),
    visual_direction: Optional[str] = None,
    copy_tone: Optional[str] = None,
    competitive_examples: Sequence[Mapping[str, Any]] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> AgentMessage:
    """Build a reply message that swaps from/to and chains parent_message_id.

    Inherits ``thread_id``, ``run_id``, ``task_type``, and ``topic`` from
    *parent* so the chain stays grouped without callers having to repeat them.
    """

    if requested_action not in REPLY_ACTIONS:
        raise ValueError(
            f"reply_to expects a reply action; got {requested_action.value}. "
            f"Use new_request for {requested_action.value}."
        )
    return AgentMessage(
        from_role=parent.to_role,
        to_role=parent.from_role,
        task_type=parent.task_type,
        topic=parent.topic,
        content=content,
        requested_action=requested_action,
        priority=priority or parent.priority,
        thread_id=parent.thread_id,
        run_id=parent.run_id,
        parent_message_id=parent.message_id,
        context_refs=tuple(context_refs),
        reference_links=tuple(reference_links),
        reference_notes=tuple(dict(item) for item in reference_notes),
        visual_direction=visual_direction,
        copy_tone=copy_tone,
        competitive_examples=tuple(dict(item) for item in competitive_examples),
        extra=dict(extra or {}),
    )


def close_thread(
    final_reply: AgentMessage,
    *,
    summary: str,
    references_used: Sequence[Mapping[str, Any]] = (),
    extra: Optional[Mapping[str, Any]] = None,
) -> AgentMessage:
    """tech-lead's outward-facing summary message that closes the round-trip.

    Sent from the role that *received* the final reply (typically tech-lead)
    back to the upstream caller (workflow gateway / discord channel /
    cto-agent). The schema reuses AgentMessage so the same transport works.
    """

    if final_reply.requested_action not in TERMINAL_REPLY_ACTIONS:
        raise ValueError(
            "close_thread expects the final reply to be COMPLETED or REJECTED; "
            f"got {final_reply.requested_action.value}."
        )
    closure_extra = {
        **dict(extra or {}),
        "round_trip_outcome": final_reply.requested_action.value,
        "references_used": [dict(item) for item in references_used],
    }
    return AgentMessage(
        from_role=final_reply.to_role,
        to_role="gateway",
        task_type=final_reply.task_type,
        topic=final_reply.topic,
        content=summary,
        requested_action=RequestedAction.ACKNOWLEDGE,
        priority=final_reply.priority,
        thread_id=final_reply.thread_id,
        run_id=final_reply.run_id,
        parent_message_id=final_reply.message_id,
        extra=closure_extra,
    )


def with_thread_id(message: AgentMessage, thread_id: str) -> AgentMessage:
    """Return a copy of *message* with ``thread_id`` set.

    Useful when transport assigns the thread id after the message is built
    (e.g. Discord creates the thread, then we pin the assignment back).
    """

    return replace(message, thread_id=thread_id)


def role_address(agent_id: str, role: str) -> str:
    """Build a normalized ``<agent>/<role>`` address string.

    Centralized so future cto-agent/design-agent code uses the same shape.
    """

    return f"{agent_id}/{role}"
