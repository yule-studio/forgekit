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
    NexusSourceRef, WeaponSpec, SkillSpec, LoadoutSpec, RuneSpec,
)

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

    def to_dict(self) -> dict:
        return {"goal": self.goal, "scope": list(self.scope),
                "forbidden_scope": list(self.forbidden_scope),
                "required_areas": list(self.required_areas), "commands": list(self.commands),
                "verification": list(self.verification), "acceptance": list(self.acceptance),
                "approval_level": self.approval_level, "evidence_path": self.evidence_path,
                "nexus_refs": [r.to_dict() for r in self.nexus_refs]}


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

    def to_dict(self) -> dict:
        return {"request": self.request, "domain": self.domain, "language": self.language,
                "framework": self.framework, "topic": self.topic,
                "candidate_agents": list(self.candidate_agents), "selected_agent": self.selected_agent,
                "selected_skills": list(self.selected_skills), "selected_loadout": self.selected_loadout,
                "required_weapons": list(self.required_weapons),
                "nexus_refs": [r.to_dict() for r in self.nexus_refs],
                "verification_commands": list(self.verification_commands),
                "packet_draft": self.packet_draft.to_dict() if self.packet_draft else None}



__all__ = (
    "NEXUS_AREA", "NEXUS_PATTERN", "NEXUS_SNIPPET", "NEXUS_TROUBLESHOOTING", "NEXUS_DECISION",
    "SRC_AVAILABLE", "SRC_NOT_CONNECTED", "SRC_PLANNED", "SRC_UNKNOWN", "SRC_EXISTS",
    "SRC_MISSING", "SRC_BLOCKED", "SRC_RESTRICTED", "WEAPON_SAFE", "WEAPON_RISKY",
    "NexusSourceRef", "WeaponSpec", "SkillSpec", "LoadoutSpec", "RuneSpec",
    "WorkPacketDraft", "ResolvedForgePlan",
)
