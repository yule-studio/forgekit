"""ForgeKit goal model (GW1) — the long-term goal record + evidence + linkage.

This is the *spine* of ForgeKit-as-control-plane: a Goal is the operator's
long-term intent that ForgeKit reads, works toward, and records evidence against.
GW1 is **model + store only** — the tick/collect/execute loop (GW4) and the
``/goal`` console surface (GW5) are separate worktrees and MUST NOT live here.

Honest boundaries kept here:
- Packet linkage is stored as **work-packet ids** (strings), NOT imported
  ``forgekit_contracts`` objects. Goal references packets; it does not own them.
  Keeps this package pure (only ``forgekit-config`` for paths) and avoids a cycle.
- Evidence is **append-only**: an evidence record is never mutated or removed by
  the model. ``done`` is gated on having at least one evidence record (see
  ``transitions``), so a goal can't be marked complete with nothing to show.

Owner: ``packages/forgekit-goal``. Roadmap/acceptance: ``docs/forgekit-goal-roadmap.md`` (GW1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

SCHEMA_VERSION = 1


class GoalStatus(str, Enum):
    """Lifecycle states of a goal. Transition rules live in ``transitions``."""

    DRAFT = "draft"
    ACTIVE = "active"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    ABANDONED = "abandoned"


# Runtime modes a goal can be bound to. Mirrors ForgeKit runtime modes
# (`forgekit_provider.policy.runtime_mode`) by *name* only — we keep the binding
# as a plain string so this pure package does not import the runtime/provider
# policy. An unknown/None value means "inherit the runtime's current mode".
KNOWN_MODES: Tuple[str, ...] = ("manual", "assisted", "auto")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_goal_id() -> str:
    """A short, sortable-enough goal id (``goal-<hex12>``)."""

    return "goal-" + uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class EvidenceRecord:
    """One append-only evidence entry attached to a goal.

    ``kind`` is a free-but-conventional tag (e.g. ``observation``, ``packet``,
    ``execution``, ``verification``, ``note``). ``ref`` optionally points at an
    external artifact (a packet id, a file path, a receipt id).
    """

    ts: str
    kind: str
    summary: str
    ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"ts": self.ts, "kind": self.kind, "summary": self.summary}
        if self.ref is not None:
            d["ref"] = self.ref
        return d

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceRecord":
        return cls(
            ts=str(raw["ts"]),
            kind=str(raw["kind"]),
            summary=str(raw["summary"]),
            ref=(str(raw["ref"]) if raw.get("ref") is not None else None),
        )


@dataclass(frozen=True)
class Goal:
    """A long-term operator goal. Immutable value object.

    All mutating helpers return a NEW ``Goal`` (frozen dataclass) with
    ``updated_at`` refreshed, so callers/stores never share aliased mutable
    state. Status changes go through ``transitions.apply`` — not by hand — so the
    transition matrix and the done-requires-evidence guard are always enforced.
    """

    id: str
    title: str
    intent: str = ""
    status: GoalStatus = GoalStatus.DRAFT
    mode: Optional[str] = None
    parent_id: Optional[str] = None
    children: Tuple[str, ...] = ()
    packets: Tuple[str, ...] = ()
    evidence: Tuple[EvidenceRecord, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    # ----- construction -------------------------------------------------
    @classmethod
    def create(
        cls,
        title: str,
        *,
        intent: str = "",
        mode: Optional[str] = None,
        parent_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        now: Callable[[], str] = _utcnow,
    ) -> "Goal":
        title = (title or "").strip()
        if not title:
            raise ValueError("goal title must be non-empty")
        if mode is not None and mode not in KNOWN_MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {KNOWN_MODES}")
        ts = now()
        return cls(
            id=goal_id or new_goal_id(),
            title=title,
            intent=intent.strip(),
            status=GoalStatus.DRAFT,
            mode=mode,
            parent_id=parent_id,
            created_at=ts,
            updated_at=ts,
        )

    # ----- immutable mutators (return a new Goal) -----------------------
    def _touch(self, now: Callable[[], str], **changes: Any) -> "Goal":
        return replace(self, updated_at=now(), **changes)

    def with_status(self, status: GoalStatus, *, now: Callable[[], str] = _utcnow) -> "Goal":
        """Low-level status set. Prefer ``transitions.apply`` which guards rules."""

        return self._touch(now, status=status)

    def add_evidence(
        self,
        kind: str,
        summary: str,
        *,
        ref: Optional[str] = None,
        now: Callable[[], str] = _utcnow,
    ) -> "Goal":
        rec = EvidenceRecord(ts=now(), kind=str(kind), summary=str(summary), ref=ref)
        return self._touch(now, evidence=self.evidence + (rec,))

    def link_packet(self, packet_id: str, *, now: Callable[[], str] = _utcnow) -> "Goal":
        packet_id = (packet_id or "").strip()
        if not packet_id:
            raise ValueError("packet_id must be non-empty")
        if packet_id in self.packets:
            return self
        return self._touch(now, packets=self.packets + (packet_id,))

    def unlink_packet(self, packet_id: str, *, now: Callable[[], str] = _utcnow) -> "Goal":
        if packet_id not in self.packets:
            return self
        return self._touch(now, packets=tuple(p for p in self.packets if p != packet_id))

    def add_child(self, child_id: str, *, now: Callable[[], str] = _utcnow) -> "Goal":
        child_id = (child_id or "").strip()
        if not child_id:
            raise ValueError("child_id must be non-empty")
        if child_id == self.id:
            raise ValueError("a goal cannot be its own child")
        if child_id in self.children:
            return self
        return self._touch(now, children=self.children + (child_id,))

    # ----- serialization ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "intent": self.intent,
            "status": self.status.value,
            "mode": self.mode,
            "parent_id": self.parent_id,
            "children": list(self.children),
            "packets": list(self.packets),
            "evidence": [e.to_dict() for e in self.evidence],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Goal":
        version = int(raw.get("schema_version", 1))
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"goal schema_version {version} is newer than supported {SCHEMA_VERSION}; upgrade forgekit-goal"
            )
        return cls(
            id=str(raw["id"]),
            title=str(raw["title"]),
            intent=str(raw.get("intent", "")),
            status=GoalStatus(str(raw.get("status", "draft"))),
            mode=(str(raw["mode"]) if raw.get("mode") is not None else None),
            parent_id=(str(raw["parent_id"]) if raw.get("parent_id") is not None else None),
            children=tuple(str(c) for c in raw.get("children", ())),
            packets=tuple(str(p) for p in raw.get("packets", ())),
            evidence=tuple(EvidenceRecord.from_dict(e) for e in raw.get("evidence", ())),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )


__all__ = (
    "SCHEMA_VERSION",
    "GoalStatus",
    "KNOWN_MODES",
    "EvidenceRecord",
    "Goal",
    "new_goal_id",
)
