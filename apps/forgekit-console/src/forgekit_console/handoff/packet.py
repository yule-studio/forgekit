"""Handoff contract — the trace + role-task + split types (pure, dependency-free).

These wrap the product-intake ``ProductIntentPacket`` with the *handoff* layer:
who forwarded it (authorship trace), the per-role task breakdown tech-lead derives,
and which areas are blocked for lack of permission. Pure dataclasses + ``to_dict``
so the whole flow is serialisable to evidence JSON and unit-testable in bare CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple

# handoff phases (authorship trace) ------------------------------------------
PHASE_INTAKE = "intake"          # PM shaped the raw ask into a packet
PHASE_GATEWAY = "gateway"        # gateway forwarded the packet to tech-lead
PHASE_TECH_LEAD = "tech-lead"    # tech-lead split the packet into role tasks

# role-task state ------------------------------------------------------------
ROLE_TASK_READY = "ready"        # can be done within forgekit's permissions
ROLE_TASK_BLOCKED = "blocked"    # needs operator (deploy / IAM / infra / secret)


@dataclass(frozen=True)
class HandoffTrace:
    """One hop in the handoff chain — who did what, in which phase, to whom."""

    phase: str
    author: str          # the acting role/agent id (e.g. "product-agent")
    author_role: str     # human label (e.g. "Product (PM)")
    handoff_from: str = ""
    handoff_to: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "author": self.author,
            "author_role": self.author_role,
            "handoff_from": self.handoff_from,
            "handoff_to": self.handoff_to,
            "note": self.note,
        }


@dataclass(frozen=True)
class RoleTask:
    """A unit of work tech-lead assigns to one engineering role."""

    role: str             # fe / be / devops / qa / security
    role_label: str
    title: str
    detail: str = ""
    state: str = ROLE_TASK_READY
    blocked_reason: str = ""   # why it can't be done here (set when state=blocked)
    needs_approval: bool = False
    runbook_hint: str = ""     # what artifact to produce instead (Terraform/ops note)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "role_label": self.role_label,
            "title": self.title,
            "detail": self.detail,
            "state": self.state,
            "blocked_reason": self.blocked_reason,
            "needs_approval": self.needs_approval,
            "runbook_hint": self.runbook_hint,
        }


@dataclass(frozen=True)
class TechLeadSplit:
    """Tech-lead's breakdown of a packet into role tasks + blocked areas."""

    tasks: Tuple[RoleTask, ...] = ()

    @property
    def blocked(self) -> Tuple[RoleTask, ...]:
        return tuple(t for t in self.tasks if t.state == ROLE_TASK_BLOCKED)

    @property
    def ready(self) -> Tuple[RoleTask, ...]:
        return tuple(t for t in self.tasks if t.state == ROLE_TASK_READY)

    def roles(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(t.role for t in self.tasks))

    def to_dict(self) -> dict:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "roles": list(self.roles()),
            "blocked_count": len(self.blocked),
        }


@dataclass(frozen=True)
class Handoff:
    """The full handoff: raw ask → packet → split, with the authorship trace chain."""

    raw_ask: str
    packet: Any                      # ProductIntentPacket (duck-typed: has to_dict)
    split: TechLeadSplit
    trace: Tuple[HandoffTrace, ...] = ()
    project: str = ""

    @property
    def has_blocked(self) -> bool:
        return bool(self.split.blocked)

    def to_dict(self) -> dict:
        packet = self.packet.to_dict() if hasattr(self.packet, "to_dict") else dict(self.packet or {})
        return {
            "raw_ask": self.raw_ask,
            "project": self.project,
            "packet": packet,
            "split": self.split.to_dict(),
            "trace": [t.to_dict() for t in self.trace],
            "has_blocked": self.has_blocked,
        }


__all__ = (
    "PHASE_INTAKE", "PHASE_GATEWAY", "PHASE_TECH_LEAD",
    "ROLE_TASK_READY", "ROLE_TASK_BLOCKED",
    "HandoffTrace", "RoleTask", "TechLeadSplit", "Handoff",
)
