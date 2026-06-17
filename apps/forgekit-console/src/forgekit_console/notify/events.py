"""Operator notification events — the action-oriented payload contract (WT4).

A notification is never just "something failed". It tells the operator: WHAT is
needed, WHY forgekit stopped, WHAT to do now, and WHICH options exist. The event
type maps to the approval-matrix ``request_type`` so the inbox + the desktop
notification describe the *same* event. Pure dataclasses → testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# event types (the set the directive requires) -------------------------------
EVENT_APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
EVENT_DECISION_REQUIRED = "DECISION_REQUIRED"
EVENT_INFO_REQUIRED = "INFO_REQUIRED"
EVENT_ACCESS_REQUIRED = "ACCESS_REQUIRED"
EVENT_SECRET_REQUIRED = "SECRET_REQUIRED"
EVENT_REPEATED_FAILURE = "REPEATED_FAILURE"
EVENT_DOCTOR_CRITICAL = "DOCTOR_CRITICAL"
EVENT_RUNTIME_BLOCKED = "RUNTIME_BLOCKED"

ALL_EVENTS: Tuple[str, ...] = (
    EVENT_APPROVAL_REQUIRED, EVENT_DECISION_REQUIRED, EVENT_INFO_REQUIRED,
    EVENT_ACCESS_REQUIRED, EVENT_SECRET_REQUIRED, EVENT_REPEATED_FAILURE,
    EVENT_DOCTOR_CRITICAL, EVENT_RUNTIME_BLOCKED,
)

# approval-matrix request_type (5 canonical) ---------------------------------
REQ_APPROVAL = "APPROVAL"
REQ_DECISION = "DECISION"
REQ_INFO = "INFO"
REQ_ACCESS = "ACCESS"
REQ_SECRET = "SECRET"

# event → request_type (the 3 derived events fold into the 5 canonical) ------
_EVENT_REQUEST = {
    EVENT_APPROVAL_REQUIRED: REQ_APPROVAL,
    EVENT_DECISION_REQUIRED: REQ_DECISION,
    EVENT_INFO_REQUIRED: REQ_INFO,
    EVENT_ACCESS_REQUIRED: REQ_ACCESS,
    EVENT_SECRET_REQUIRED: REQ_SECRET,
    EVENT_REPEATED_FAILURE: REQ_DECISION,
    EVENT_DOCTOR_CRITICAL: REQ_INFO,
    EVENT_RUNTIME_BLOCKED: REQ_DECISION,
}


def request_type_for(event_type: str) -> str:
    return _EVENT_REQUEST.get(event_type, REQ_INFO)


@dataclass(frozen=True)
class NotificationEvent:
    """An operator-actionable event — the same payload for inbox + desktop."""

    event_type: str
    title: str
    why: str                      # why forgekit stopped / needs the operator
    action: str                   # what the operator should do now
    options: Tuple[str, ...] = ()  # the choices, when it's a decision
    source: str = ""              # which subsystem raised it (mode/runtime/doctor)

    @property
    def request_type(self) -> str:
        return request_type_for(self.event_type)

    def desktop_body(self) -> str:
        """A concise, action-oriented body for the desktop notification."""

        body = f"{self.why} · 지금: {self.action}"
        return body if len(body) <= 200 else body[:197] + "…"

    def to_inbox_entry(self) -> dict:
        return {
            "request_type": self.request_type,
            "event_type": self.event_type,
            "title": self.title,
            "why": self.why,
            "action": self.action,
            "options": list(self.options),
            "source": self.source,
            "needs_operator": True,
        }


__all__ = (
    "EVENT_APPROVAL_REQUIRED", "EVENT_DECISION_REQUIRED", "EVENT_INFO_REQUIRED",
    "EVENT_ACCESS_REQUIRED", "EVENT_SECRET_REQUIRED", "EVENT_REPEATED_FAILURE",
    "EVENT_DOCTOR_CRITICAL", "EVENT_RUNTIME_BLOCKED", "ALL_EVENTS",
    "REQ_APPROVAL", "REQ_DECISION", "REQ_INFO", "REQ_ACCESS", "REQ_SECRET",
    "request_type_for", "NotificationEvent",
)
