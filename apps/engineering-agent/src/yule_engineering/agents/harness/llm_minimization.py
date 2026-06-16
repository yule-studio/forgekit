"""Rule-first LLM minimization policy — declare what must NOT go to an LLM.

Token efficiency so far reduced *how much* we send. This module declares, per
input capability class, *whether to call an LLM at all*:

    resolution_mode ∈ {rule_first, llm_optional, llm_required}

  * ``rule_first``    — resolve by rule/deterministic; skip live LLM when
    possible (classification, enforcement, security_gate, verification,
    grant/policy/receipt formatting, cache-hit fast paths).
  * ``llm_optional``  — an LLM helps but a cheap/local model (Ollama/Gemini)
    should be tried first (summarization, compaction).
  * ``llm_required``  — keep the strong-LLM path (research, execution, delivery,
    exploration).

The decision feeds routing (skip/cheap/strong), the execution receipt (why a
run did or didn't use an LLM), and insights (how many calls were avoided).

Pure + deterministic, no routing/runner imports. Explicit metadata overrides the
capability mapping. Default (unknown capability, no override) is
``llm_required`` — the safe, backward-compatible behavior (LLM allowed, no bypass).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

RESOLUTION_RULE_FIRST = "rule_first"
RESOLUTION_LLM_OPTIONAL = "llm_optional"
RESOLUTION_LLM_REQUIRED = "llm_required"

RESOLUTION_MODES: Tuple[str, ...] = (
    RESOLUTION_RULE_FIRST,
    RESOLUTION_LLM_OPTIONAL,
    RESOLUTION_LLM_REQUIRED,
)

# capability_class → resolution_mode (the declarative SSoT).
CAPABILITY_RESOLUTION: Mapping[str, str] = {
    # rule-first: deterministic / policy / formatting / fast-path
    "classification": RESOLUTION_RULE_FIRST,
    "enforcement": RESOLUTION_RULE_FIRST,
    "security_gate": RESOLUTION_RULE_FIRST,
    "verification": RESOLUTION_RULE_FIRST,
    "memory": RESOLUTION_RULE_FIRST,
    # llm-optional: cheap/local first
    "summarization": RESOLUTION_LLM_OPTIONAL,
    "compaction": RESOLUTION_LLM_OPTIONAL,
    # llm-required: strong path
    "research": RESOLUTION_LLM_REQUIRED,
    "execution": RESOLUTION_LLM_REQUIRED,
    "delivery": RESOLUTION_LLM_REQUIRED,
    "exploration": RESOLUTION_LLM_REQUIRED,
}

# Backward-compatible default when nothing is known: keep the LLM path.
DEFAULT_RESOLUTION = RESOLUTION_LLM_REQUIRED


@dataclass(frozen=True)
class ResolutionDecision:
    capability_class: Optional[str]
    resolution_mode: str
    llm_allowed: bool
    why: str

    def to_dict(self) -> dict:
        return {
            "capability_class": self.capability_class,
            "resolution_mode": self.resolution_mode,
            "llm_allowed": self.llm_allowed,
            "why": self.why,
        }


def _llm_allowed_for(mode: str) -> bool:
    # rule_first prefers no LLM; the other modes allow it.
    return mode != RESOLUTION_RULE_FIRST


def resolve(
    capability_class: Optional[str] = None,
    *,
    explicit_mode: Optional[str] = None,
    explicit_llm_allowed: Optional[bool] = None,
) -> ResolutionDecision:
    """Resolve the LLM-minimization decision for a capability class.

    *explicit_mode* / *explicit_llm_allowed* (from input metadata) override the
    capability mapping. An unknown capability with no override resolves to
    :data:`DEFAULT_RESOLUTION` (llm_required) — never silently bypasses an LLM.
    """

    cc = (capability_class or "").strip().lower() or None

    if explicit_mode and explicit_mode.strip().lower() in RESOLUTION_MODES:
        mode = explicit_mode.strip().lower()
        allowed = explicit_llm_allowed if explicit_llm_allowed is not None else _llm_allowed_for(mode)
        return ResolutionDecision(cc, mode, bool(allowed), why=f"explicit:{mode}")

    if cc and cc in CAPABILITY_RESOLUTION:
        mode = CAPABILITY_RESOLUTION[cc]
        allowed = explicit_llm_allowed if explicit_llm_allowed is not None else _llm_allowed_for(mode)
        return ResolutionDecision(cc, mode, bool(allowed), why=f"capability:{cc}->{mode}")

    mode = DEFAULT_RESOLUTION
    allowed = explicit_llm_allowed if explicit_llm_allowed is not None else _llm_allowed_for(mode)
    return ResolutionDecision(cc, mode, bool(allowed), why="default:no_capability")


def resolve_from_metadata(
    metadata: Optional[Mapping[str, object]], capability_class: Optional[str] = None
) -> ResolutionDecision:
    """Resolve from a RoleRunnerInput's metadata (explicit override aware)."""

    md = metadata or {}
    if not isinstance(md, Mapping):
        md = {}
    explicit_mode = _opt_str(md.get("resolution_mode"))
    explicit_llm = md.get("llm_allowed")
    explicit_llm = bool(explicit_llm) if isinstance(explicit_llm, bool) else None
    cc = capability_class or _opt_str(md.get("capability_class"))
    return resolve(cc, explicit_mode=explicit_mode, explicit_llm_allowed=explicit_llm)


def _opt_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = (
    "RESOLUTION_RULE_FIRST",
    "RESOLUTION_LLM_OPTIONAL",
    "RESOLUTION_LLM_REQUIRED",
    "RESOLUTION_MODES",
    "CAPABILITY_RESOLUTION",
    "DEFAULT_RESOLUTION",
    "ResolutionDecision",
    "resolve",
    "resolve_from_metadata",
)
