"""Hephaistos forge-output models — WorkPacketDraft / ResolvedForgePlan. Pure / stdlib.

The catalog/capability vocabulary (Skills/Loadouts/Weapons/Runes + NexusSourceRef) moved
to ``armory.models`` (RWT2) and is re-exported here for backward compatibility, so
``from hephaistos.models import SkillSpec`` keeps working. New code should import catalog
types from ``armory.models`` and forge-output types from here. See ``docs/armory.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from armory.models import (  # noqa: F401  (re-export catalog vocab for compat)
    NEXUS_AREA, NEXUS_PATTERN, NEXUS_SNIPPET, NEXUS_TROUBLESHOOTING, NEXUS_DECISION,
    SRC_AVAILABLE, SRC_NOT_CONNECTED, SRC_PLANNED, SRC_UNKNOWN, SRC_EXISTS,
    SRC_MISSING, SRC_BLOCKED, SRC_RESTRICTED, WEAPON_SAFE, WEAPON_RISKY,
    KIND_SKILL, KIND_TOOL, KIND_PLUGIN, KIND_MCP, ENTRY_KINDS, ATTACH_REQUIRED_KINDS,
    NexusSourceRef, WeaponSpec, SkillSpec, LoadoutSpec, RuneSpec,
)


@dataclass(frozen=True)
class SelectionEvidence:
    """Why one item was (de)selected — the anti-fake trail behind every Hephaistos pick.

    ``target`` is a skill/loadout/weapon id; ``kind`` is what it is; ``decision`` is
    selected/excluded; ``reason`` + ``signals`` say WHAT in the request/context drove it.
    No selection ships without a row here — a "smart" pick with no evidence is a fake.
    """

    target: str
    kind: str                 # skill / loadout / weapon / agent / constraint
    decision: str             # selected / excluded
    reason: str = ""
    signals: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"target": self.target, "kind": self.kind, "decision": self.decision,
                "reason": self.reason, "signals": list(self.signals)}


@dataclass(frozen=True)
class RejectedCandidate:
    """A skill/tool the resolver *considered and declined* — with the concrete reason.

    Distinct from "never matched": these were plausible (language-gated alternative,
    project-fact exclusion, loadout-scoped-out) and actively dropped, so a scenario can
    show *why not* — the anti-fake counterpart to the selection trail.
    """

    target: str
    kind: str = "skill"       # skill / tool
    reason: str = ""
    category: str = ""        # rejection class: language-gate / project-fact / loadout-scope

    def to_dict(self) -> dict:
        return {"target": self.target, "kind": self.kind, "reason": self.reason,
                "category": self.category}


@dataclass(frozen=True)
class WorkPacketDraft:
    goal: str
    scope: Tuple[str, ...] = ()
    forbidden_scope: Tuple[str, ...] = ()
    required_areas: Tuple[str, ...] = ()
    commands: Tuple[str, ...] = ()
    verification: Tuple[str, ...] = ()
    acceptance: Tuple[str, ...] = ()
    approval_level: str = "L2_internal_approve"
    evidence_path: str = ""
    nexus_refs: Tuple[NexusSourceRef, ...] = ()
    # work-packet quality fields — concrete + execution-ready.
    selected_tools: Tuple[str, ...] = ()       # weapon ids the executor must have/attach
    constraints: Tuple[str, ...] = ()          # project-fact constraints (dev-first / keep-structure …)
    harness: str = ""                          # intended executor harness (claude-code / codex …)

    def to_dict(self) -> dict:
        return {"goal": self.goal, "scope": list(self.scope),
                "forbidden_scope": list(self.forbidden_scope),
                "required_areas": list(self.required_areas), "commands": list(self.commands),
                "verification": list(self.verification), "acceptance": list(self.acceptance),
                "approval_level": self.approval_level, "evidence_path": self.evidence_path,
                "nexus_refs": [r.to_dict() for r in self.nexus_refs],
                "selected_tools": list(self.selected_tools), "constraints": list(self.constraints),
                "harness": self.harness}


@dataclass(frozen=True)
class ResolvedForgePlan:
    """What Hephaistos forged for one request — the equip plan (no install performed)."""

    request: str
    domain: str = ""
    language: str = ""
    framework: str = ""
    topic: str = ""
    candidate_agents: Tuple[str, ...] = ()
    selected_agent: str = ""
    selected_skills: Tuple[str, ...] = ()
    selected_loadout: str = ""
    required_weapons: Tuple[str, ...] = ()
    nexus_refs: Tuple[NexusSourceRef, ...] = ()
    verification_commands: Tuple[str, ...] = ()
    packet_draft: WorkPacketDraft = None  # type: ignore[assignment]
    # anti-fake selection trail + what context shaped it.
    selection_evidence: Tuple[SelectionEvidence, ...] = ()
    excluded_skills: Tuple[str, ...] = ()      # dropped by a project fact (e.g. "EKS 제외")
    project_facts: Tuple[str, ...] = ()        # Nexus/operator facts fed into selection
    runtime_constraints: Tuple[str, ...] = ()  # provider/runtime constraints fed in
    rejected_candidates: Tuple[RejectedCandidate, ...] = ()  # considered + declined (why-not)

    def to_dict(self) -> dict:
        return {"request": self.request, "domain": self.domain, "language": self.language,
                "framework": self.framework, "topic": self.topic,
                "candidate_agents": list(self.candidate_agents), "selected_agent": self.selected_agent,
                "selected_skills": list(self.selected_skills), "selected_loadout": self.selected_loadout,
                "required_weapons": list(self.required_weapons),
                "nexus_refs": [r.to_dict() for r in self.nexus_refs],
                "verification_commands": list(self.verification_commands),
                "packet_draft": self.packet_draft.to_dict() if self.packet_draft else None,
                "selection_evidence": [e.to_dict() for e in self.selection_evidence],
                "excluded_skills": list(self.excluded_skills),
                "project_facts": list(self.project_facts),
                "runtime_constraints": list(self.runtime_constraints),
                "rejected_candidates": [r.to_dict() for r in self.rejected_candidates]}



__all__ = (
    "NEXUS_AREA", "NEXUS_PATTERN", "NEXUS_SNIPPET", "NEXUS_TROUBLESHOOTING", "NEXUS_DECISION",
    "SRC_AVAILABLE", "SRC_NOT_CONNECTED", "SRC_PLANNED", "SRC_UNKNOWN", "SRC_EXISTS",
    "SRC_MISSING", "SRC_BLOCKED", "SRC_RESTRICTED", "WEAPON_SAFE", "WEAPON_RISKY",
    "KIND_SKILL", "KIND_TOOL", "KIND_PLUGIN", "KIND_MCP", "ENTRY_KINDS", "ATTACH_REQUIRED_KINDS",
    "NexusSourceRef", "WeaponSpec", "SkillSpec", "LoadoutSpec", "RuneSpec",
    "WorkPacketDraft", "ResolvedForgePlan", "SelectionEvidence", "RejectedCandidate",
)
