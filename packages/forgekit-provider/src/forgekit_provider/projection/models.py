"""Provider projection vocabulary — taxonomy kind × capability class → projection verdict.

This is the *provider projection lane* SSoT: a vendor-neutral tool/skill/plugin candidate
is described once (``ToolCandidate``) and deterministically routed to the provider
ecosystem(s) it belongs in (``ProjectionVerdict``). It encodes the separation pinned by
``docs/plugin-taxonomy.md`` + ``docs/provider-capability-matrix.md``:

  * **Claude / Codex / Gemini are projection targets** — a capability is *projected* into
    each one's native expression (hook / skill / command / MCP).
  * **Ollama is a backend slot** — a local inference / tool-calling endpoint, NEVER a
    projection target. The two concepts must not be mixed; this module keeps them in
    separate fields (``projection_targets`` vs ``backend_role``).

Pure / stdlib-only. No live connection is asserted here — the verdict carries the *honest
condition* (attach / connect / verify) under which a candidate becomes real, never a
fake "connected" claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# ── taxonomy kinds (the 6 axes of docs/plugin-taxonomy.md §1) ───────────────────────────
KIND_BACKEND = "backend"                      # an LLM runner / inference engine (not projected)
KIND_SKILL = "skill"                          # reusable agent procedure (projected to harnesses)
KIND_HOOK = "hook"                            # lifecycle intercept (pre/post/gate)
KIND_MCP = "mcp"                              # external tool server (Model Context Protocol)
KIND_RUNTIME_PLUGIN = "runtime_plugin"        # Yule runtime hook-provider module (vendor-neutral)
KIND_HARNESS_PROJECTION = "harness_projection"  # an already-generated provider bundle

TAXONOMY_KINDS = (
    KIND_BACKEND, KIND_SKILL, KIND_HOOK, KIND_MCP, KIND_RUNTIME_PLUGIN, KIND_HARNESS_PROJECTION,
)

# ── projection targets vs backend slot — NEVER mixed ────────────────────────────────────
TARGET_CLAUDE = "claude"
TARGET_CODEX = "codex"
TARGET_GEMINI = "gemini"
PROJECTION_TARGETS = (TARGET_CLAUDE, TARGET_CODEX, TARGET_GEMINI)

BACKEND_OLLAMA = "ollama"                      # the local-inference backend slot (NOT a target)

# ── capability classes (vendor-neutral lens; superset of the governance taxonomy set) ───
# Projection-bound classes route to Claude/Codex/Gemini; backend classes route to Ollama.
CAP_SECURITY_GATE = "security_gate"
CAP_ENFORCEMENT = "enforcement"
CAP_VERIFICATION = "verification"
CAP_COMPACTION = "compaction"
CAP_MEMORY = "memory"
CAP_EXPLORATION = "exploration"
CAP_DELIVERY = "delivery"
CAP_EXECUTION = "execution"
CAP_TOOL_USE = "tool_use"                      # browser / computer-use / figma manipulation
CAP_INTEGRATION = "integration"                # github / slack / notion execution
CAP_RESEARCH = "research"                      # large-context reading / retrieval
CAP_ANALYSIS = "analysis"                      # cheap analysis / draft generation
# backend (local-inference) classes — route to Ollama, never a projection target
CAP_CLASSIFICATION = "classification"
CAP_SUMMARIZATION = "summarization"
CAP_COMPRESSION = "compression"
CAP_FALLBACK = "fallback"

BACKEND_CAPABILITIES = (
    CAP_CLASSIFICATION, CAP_SUMMARIZATION, CAP_COMPRESSION, CAP_FALLBACK,
)

CAPABILITY_CLASSES = (
    CAP_SECURITY_GATE, CAP_ENFORCEMENT, CAP_VERIFICATION, CAP_COMPACTION, CAP_MEMORY,
    CAP_EXPLORATION, CAP_DELIVERY, CAP_EXECUTION, CAP_TOOL_USE, CAP_INTEGRATION,
    CAP_RESEARCH, CAP_ANALYSIS, *BACKEND_CAPABILITIES,
)


@dataclass(frozen=True)
class ToolCandidate:
    """A vendor-neutral skill/plugin/tool candidate to be projected onto provider ecosystems.

    ``capability_class`` is the routing lens (vendor-neutral — NEVER a provider name).
    ``taxonomy_kind`` constrains *how* it can be expressed (a hook can't project to Gemini,
    an MCP server can't run on Ollama, etc.). ``verify_command`` carries a concrete check
    when the candidate is a CLI/tool weapon.
    """

    id: str
    name: str
    taxonomy_kind: str = KIND_SKILL
    capability_class: str = CAP_EXECUTION
    summary: str = ""
    verify_command: str = ""        # concrete presence check, when known (weapons)
    source: str = ""                # where the candidate came from (armory / discovery / manifest)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "taxonomy_kind": self.taxonomy_kind,
                "capability_class": self.capability_class, "summary": self.summary,
                "verify_command": self.verify_command, "source": self.source}


@dataclass(frozen=True)
class TargetPlan:
    """Per-target projection conditions — how a candidate attaches / connects / verifies."""

    target: str                     # a PROJECTION_TARGET or BACKEND_OLLAMA
    attach: str = ""                # how it gets attached to this ecosystem
    connect: str = ""               # what live connection it needs (honest, no fake)
    verify: str = ""                # how to verify it actually works
    has_connector: bool = True      # False → no generated connector yet (manual / honest)

    def to_dict(self) -> dict:
        return {"target": self.target, "attach": self.attach, "connect": self.connect,
                "verify": self.verify, "has_connector": self.has_connector}


@dataclass(frozen=True)
class ProjectionVerdict:
    """The deterministic routing result. Projection targets and the backend role are kept in
    SEPARATE fields so the two concepts can never be silently merged."""

    candidate: ToolCandidate
    primary_target: str = ""                          # the strongest projection target (or "")
    projection_targets: Tuple[str, ...] = ()          # subset of Claude/Codex/Gemini
    backend_role: str = ""                            # BACKEND_OLLAMA when local-inference, else ""
    plans: Tuple[TargetPlan, ...] = ()                # attach/connect/verify per target
    rationale: str = ""

    @property
    def is_backend(self) -> bool:
        """True when this candidate is a backend slot (Ollama), not a projection."""
        return bool(self.backend_role)

    @property
    def is_neutral_runtime(self) -> bool:
        """True for vendor-neutral runtime plugins with no single primary projection target."""
        return not self.primary_target and not self.backend_role

    def plan_for(self, target: str):
        return next((p for p in self.plans if p.target == target), None)

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict(),
            "primary_target": self.primary_target,
            "projection_targets": list(self.projection_targets),
            "backend_role": self.backend_role,
            "is_backend": self.is_backend,
            "plans": [p.to_dict() for p in self.plans],
            "rationale": self.rationale,
        }


__all__ = (
    "KIND_BACKEND", "KIND_SKILL", "KIND_HOOK", "KIND_MCP", "KIND_RUNTIME_PLUGIN",
    "KIND_HARNESS_PROJECTION", "TAXONOMY_KINDS",
    "TARGET_CLAUDE", "TARGET_CODEX", "TARGET_GEMINI", "PROJECTION_TARGETS", "BACKEND_OLLAMA",
    "CAP_SECURITY_GATE", "CAP_ENFORCEMENT", "CAP_VERIFICATION", "CAP_COMPACTION", "CAP_MEMORY",
    "CAP_EXPLORATION", "CAP_DELIVERY", "CAP_EXECUTION", "CAP_TOOL_USE", "CAP_INTEGRATION",
    "CAP_RESEARCH", "CAP_ANALYSIS", "CAP_CLASSIFICATION", "CAP_SUMMARIZATION",
    "CAP_COMPRESSION", "CAP_FALLBACK", "BACKEND_CAPABILITIES", "CAPABILITY_CLASSES",
    "ToolCandidate", "TargetPlan", "ProjectionVerdict",
)
