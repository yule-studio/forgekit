"""armory.models — the catalog/capability vocabulary (Skills/Loadouts/Weapons/Runes
and the NexusSourceRef a spec declares). Pure / stdlib-only.

Owner: ``packages/armory`` (ForgeKit named core, RWT2). Split out of
``hephaistos.models`` so the "what exists / how a capability is specified" vocabulary
lives with the Armory catalog, while Hephaistos keeps the forge-output types
(WorkPacketDraft / ResolvedForgePlan). Armory depends on nothing here → no cycle with
Hephaistos. See ``docs/package-topology.md`` and ``docs/armory.md``.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# nexus source kinds + connection status
NEXUS_AREA = "area"
NEXUS_PATTERN = "pattern"
NEXUS_SNIPPET = "snippet"
NEXUS_TROUBLESHOOTING = "troubleshooting"
NEXUS_DECISION = "decision"

SRC_AVAILABLE = "available"
SRC_NOT_CONNECTED = "not_connected"   # Nexus mount/config absent (planned seam)
SRC_PLANNED = "planned"
# exists_status values used by the read path (PR1):
SRC_UNKNOWN = "unknown"               # not yet resolved
SRC_EXISTS = "exists"                 # the file is present + readable
SRC_MISSING = "missing"              # Nexus connected but the path is absent
SRC_BLOCKED = "blocked"              # present but unreadable (permission/TCC/sandbox)
SRC_RESTRICTED = "restricted"        # present but raw read gated → projection only

# weapon safety class
WEAPON_SAFE = "safe"
WEAPON_RISKY = "risky"

# entry kind — what *shape* of capability a catalog entry is. The selection contract
# differs by kind: a tool/mcp/plugin needs install/attach requirements before it can be
# equipped, while a pure skill is knowledge/workflow the executor already carries.
KIND_SKILL = "skill"     # knowledge / workflow / convention (no external attach)
KIND_TOOL = "tool"       # a CLI / binary the executor invokes (needs install)
KIND_PLUGIN = "plugin"   # a harness plugin / extension (needs attach to a harness)
KIND_MCP = "mcp"         # an MCP server (needs connect/attach + transport)
ENTRY_KINDS = (KIND_SKILL, KIND_TOOL, KIND_PLUGIN, KIND_MCP)
# kinds that cannot be equipped without an explicit install/attach step.
ATTACH_REQUIRED_KINDS = (KIND_TOOL, KIND_PLUGIN, KIND_MCP)


@dataclass(frozen=True)
class NexusSourceRef:
    kind: str            # NEXUS_*
    ref: str             # e.g. "20-areas/backend/java-spring"  (the path)
    status: str = SRC_NOT_CONNECTED   # exists_status (SRC_*) — resolved by the read path
    note: str = ""
    title_hint: str = ""
    tags: Tuple[str, ...] = ()
    priority: int = 0
    restricted: bool = False
    required_for_skill: str = ""
    source_repo: str = "nexus"

    @property
    def path(self) -> str:           # spec alias — ``ref`` IS the path
        return self.ref

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "status": self.status, "note": self.note,
                "title_hint": self.title_hint, "tags": list(self.tags), "priority": self.priority,
                "restricted": self.restricted, "required_for_skill": self.required_for_skill,
                "source_repo": self.source_repo}


@dataclass(frozen=True)
class WeaponSpec:
    id: str
    display_name: str
    kind: str = "tool"           # tool / runtime / service / cli / ide
    verify_command: str = ""     # how to check presence (e.g. "java -version")
    install_hint: str = ""
    safety: str = WEAPON_SAFE

    def to_dict(self) -> dict:
        return {"id": self.id, "display_name": self.display_name, "kind": self.kind,
                "verify_command": self.verify_command, "install_hint": self.install_hint,
                "safety": self.safety}


@dataclass(frozen=True)
class SkillSpec:
    id: str
    name: str
    domains: Tuple[str, ...] = ()
    languages: Tuple[str, ...] = ()
    frameworks: Tuple[str, ...] = ()
    topics: Tuple[str, ...] = ()
    rules: Tuple[str, ...] = ()
    commands: Tuple[str, ...] = ()
    verification: Tuple[str, ...] = ()
    forbidden: Tuple[str, ...] = ()
    related_weapons: Tuple[str, ...] = ()
    related_loadouts: Tuple[str, ...] = ()
    related_roles: Tuple[str, ...] = ()
    nexus_refs: Tuple[NexusSourceRef, ...] = ()
    # breadth fields (PR3) — the real selection contract.
    category: str = ""
    summary: str = ""
    when_to_use: Tuple[str, ...] = ()
    when_not_to_use: Tuple[str, ...] = ()
    required_inputs: Tuple[str, ...] = ()
    expected_outputs: Tuple[str, ...] = ()
    signals: Tuple[str, ...] = ()          # keyword/intent signals the resolver scores on
    capability_note: str = ""              # vendor-neutral capability (NOT a provider name)
    status: str = "ready"                  # ready / partial / shallow
    # entry-kind + attach contract (RWT2 intake) — a tool/mcp/plugin is only equippable
    # once its install/attach requirements are met. ``provider_affinity`` names the
    # harness/runtime an entry attaches to (claude-code / codex / mcp-host …) — this is
    # an *attachment target*, NOT a capability claim, so it is allowed to name a vendor
    # (``capability_note`` stays vendor-neutral; the breadth test guards only that field).
    kind: str = KIND_SKILL
    provider_affinity: Tuple[str, ...] = ()   # harness/runtime targets (claude-code/codex/…)
    install_requirements: Tuple[str, ...] = ()  # what must exist locally (weapon/cli/runtime)
    attach_requirements: Tuple[str, ...] = ()   # how to attach (mcp connect / plugin enable …)

    @property
    def title(self) -> str:
        return self.name

    @property
    def needs_attach(self) -> bool:
        return self.kind in ATTACH_REQUIRED_KINDS

    @property
    def unsafe_boundary(self) -> Tuple[str, ...]:
        return self.forbidden

    @property
    def verify_steps(self) -> Tuple[str, ...]:
        return self.verification

    def signal_score(self, blob: str) -> int:
        return sum(1 for s in self.signals if s and s in blob)

    def matches(self, *, domain="", language="", framework="", topic="") -> int:
        """Heuristic relevance score for a resolve query (deterministic)."""

        score = 0
        if domain and domain in self.domains:
            score += 2
        if language and language in self.languages:
            score += 2
        if framework and framework in self.frameworks:
            score += 3
        if topic and topic in self.topics:
            score += 4
        return score

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "domains": list(self.domains),
                "languages": list(self.languages), "frameworks": list(self.frameworks),
                "topics": list(self.topics), "rules": list(self.rules),
                "commands": list(self.commands), "verification": list(self.verification),
                "forbidden": list(self.forbidden), "related_weapons": list(self.related_weapons),
                "related_loadouts": list(self.related_loadouts),
                "related_roles": list(self.related_roles),
                "nexus_refs": [r.to_dict() for r in self.nexus_refs],
                "category": self.category, "summary": self.summary,
                "when_to_use": list(self.when_to_use), "when_not_to_use": list(self.when_not_to_use),
                "required_inputs": list(self.required_inputs),
                "expected_outputs": list(self.expected_outputs),
                "signals": list(self.signals), "capability_note": self.capability_note,
                "status": self.status, "kind": self.kind,
                "provider_affinity": list(self.provider_affinity),
                "install_requirements": list(self.install_requirements),
                "attach_requirements": list(self.attach_requirements)}


@dataclass(frozen=True)
class LoadoutSpec:
    id: str
    name: str
    intended_roles: Tuple[str, ...] = ()
    required_weapons: Tuple[str, ...] = ()
    optional_weapons: Tuple[str, ...] = ()
    environment_assumptions: Tuple[str, ...] = ()
    verify_commands: Tuple[str, ...] = ()
    # breadth fields (PR3)
    goal: str = ""
    recommended_skills: Tuple[str, ...] = ()
    optional_skills: Tuple[str, ...] = ()
    blocked_skills: Tuple[str, ...] = ()
    default_verify_flow: Tuple[str, ...] = ()
    selection_signals: Tuple[str, ...] = ()    # signals that pick THIS loadout
    notes: str = ""

    @property
    def title(self) -> str:
        return self.name

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "intended_roles": list(self.intended_roles),
                "required_weapons": list(self.required_weapons),
                "optional_weapons": list(self.optional_weapons),
                "environment_assumptions": list(self.environment_assumptions),
                "verify_commands": list(self.verify_commands), "goal": self.goal,
                "recommended_skills": list(self.recommended_skills),
                "optional_skills": list(self.optional_skills),
                "blocked_skills": list(self.blocked_skills),
                "default_verify_flow": list(self.default_verify_flow),
                "selection_signals": list(self.selection_signals), "notes": self.notes}


@dataclass(frozen=True)
class RuneSpec:
    id: str
    name: str
    kind: str = "preset"         # env / config / alias / template
    template_ref: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "template_ref": self.template_ref}



__all__ = (
    "NEXUS_AREA", "NEXUS_PATTERN", "NEXUS_SNIPPET", "NEXUS_TROUBLESHOOTING", "NEXUS_DECISION",
    "SRC_AVAILABLE", "SRC_NOT_CONNECTED", "SRC_PLANNED", "SRC_UNKNOWN", "SRC_EXISTS",
    "SRC_MISSING", "SRC_BLOCKED", "SRC_RESTRICTED", "WEAPON_SAFE", "WEAPON_RISKY",
    "KIND_SKILL", "KIND_TOOL", "KIND_PLUGIN", "KIND_MCP", "ENTRY_KINDS", "ATTACH_REQUIRED_KINDS",
    "NexusSourceRef", "WeaponSpec", "SkillSpec", "LoadoutSpec", "RuneSpec",
)
