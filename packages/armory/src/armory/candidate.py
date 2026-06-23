"""armory.candidate — the intake → catalog promotion path (RWT2).

An ``ArmoryCandidate`` is a *proposal* for a new catalog entry: a skill / tool / plugin /
MCP surfaced by discovery (Nexus sweep), a curated note, or an operator. It is NOT yet in
the catalog. ``promote_candidate`` is the gate that turns a proposal into a real
``SkillSpec`` — but only if it carries a non-placeholder contract: summary, when_to_use,
signals, an unsafe boundary, a capability lens, and (for tool/plugin/mcp kinds) the
install/attach requirements needed to actually equip it.

Promotion is **explainable and honest**: a rejection lists every missing gate; an accept
carries the evidence of which gates passed. There is no silent "looks fine" — an
incomplete candidate is rejected, never half-promoted with placeholder fields. Pure /
stdlib-only; depends only on ``armory.models`` (no Hephaistos import → no cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .models import (
    ATTACH_REQUIRED_KINDS,
    ENTRY_KINDS,
    KIND_SKILL,
    NexusSourceRef,
    SkillSpec,
)

# placeholder tokens that disqualify a "contract" field — a candidate that still carries
# these is a stub, not a real entry, and must not enter the catalog.
_PLACEHOLDERS = ("tbd", "todo", "fixme", "xxx", "placeholder", "...", "내용 없음", "(미정)")

# vendor names that may not appear in capability_note (matches the breadth test guard).
_VENDOR_TOKENS = ("claude", "codex", "gemini", "gpt-4", "openai gpt")

_MIN_SUMMARY = 6   # a real summary is at least a few chars, not "x"


def _is_placeholder(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return any(p in t for p in _PLACEHOLDERS)


@dataclass(frozen=True)
class ArmoryCandidate:
    """A proposed catalog entry awaiting promotion (intake side)."""

    id: str
    name: str
    kind: str = KIND_SKILL
    category: str = ""
    summary: str = ""
    domains: Tuple[str, ...] = ()
    languages: Tuple[str, ...] = ()
    frameworks: Tuple[str, ...] = ()
    topics: Tuple[str, ...] = ()
    signals: Tuple[str, ...] = ()
    when_to_use: Tuple[str, ...] = ()
    when_not_to_use: Tuple[str, ...] = ()
    required_inputs: Tuple[str, ...] = ()
    expected_outputs: Tuple[str, ...] = ()
    unsafe_boundary: Tuple[str, ...] = ()        # → SkillSpec.forbidden
    capability_note: str = ""
    provider_affinity: Tuple[str, ...] = ()
    install_requirements: Tuple[str, ...] = ()
    attach_requirements: Tuple[str, ...] = ()
    commands: Tuple[str, ...] = ()
    verification: Tuple[str, ...] = ()
    rules: Tuple[str, ...] = ()
    related_weapons: Tuple[str, ...] = ()
    related_loadouts: Tuple[str, ...] = ()
    related_roles: Tuple[str, ...] = ()
    nexus_refs: Tuple[NexusSourceRef, ...] = ()
    status: str = "ready"
    # provenance — where this candidate came from (discovery brief id / curated note / operator).
    source: str = "operator"
    source_ref: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "category": self.category,
                "summary": self.summary, "signals": list(self.signals),
                "when_to_use": list(self.when_to_use), "unsafe_boundary": list(self.unsafe_boundary),
                "capability_note": self.capability_note,
                "install_requirements": list(self.install_requirements),
                "attach_requirements": list(self.attach_requirements),
                "source": self.source, "source_ref": self.source_ref}


@dataclass(frozen=True)
class PromotionResult:
    """The verdict of one promotion attempt — accepted (with spec) or rejected (with reasons)."""

    candidate_id: str
    accepted: bool
    spec: Optional[SkillSpec] = None
    reasons: Tuple[str, ...] = ()        # why rejected (empty if accepted)
    evidence: Tuple[str, ...] = ()       # which gates passed / which fields carried the entry

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, "accepted": self.accepted,
                "spec": self.spec.to_dict() if self.spec else None,
                "reasons": list(self.reasons), "evidence": list(self.evidence)}


def _validate(c: ArmoryCandidate) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return (reasons_to_reject, evidence_of_passed_gates). Deterministic + explainable."""

    reasons: list = []
    evidence: list = []

    if not c.id or not c.id.strip():
        reasons.append("id 없음")
    if c.kind not in ENTRY_KINDS:
        reasons.append(f"kind 불명: {c.kind!r} (skill/tool/plugin/mcp 중 하나)")
    else:
        evidence.append(f"kind={c.kind}")

    if not c.category:
        reasons.append("category 없음")
    if _is_placeholder(c.summary) or len(c.summary.strip()) < _MIN_SUMMARY:
        reasons.append("summary 가 placeholder/너무 짧음")
    else:
        evidence.append("summary 실체 있음")

    if not c.signals:
        reasons.append("signals 없음 — resolver 가 고를 신호가 없다")
    else:
        evidence.append(f"signals {len(c.signals)}개")

    if not c.when_to_use:
        reasons.append("when_to_use 없음 — 언제 쓰는지 불명")
    else:
        evidence.append("when_to_use 명시")

    if not c.unsafe_boundary:
        reasons.append("unsafe_boundary 없음 — 위험 경계 미선언(금지)")
    else:
        evidence.append("unsafe_boundary 선언")

    if not c.capability_note:
        reasons.append("capability_note 없음")
    else:
        low = c.capability_note.lower()
        vendor = next((v for v in _VENDOR_TOKENS if v in low), "")
        if vendor:
            reasons.append(f"capability_note 가 vendor 명시({vendor}) — provider-neutral 위반")
        else:
            evidence.append("capability_note vendor-neutral")

    if not (c.commands or c.verification):
        reasons.append("commands/verification 둘 다 없음 — 검증 경로 없음")
    else:
        evidence.append("검증 경로 있음")

    # attach contract: a tool/plugin/mcp must declare how it is installed/attached, else
    # it cannot be equipped — promoting it would be a fake "available" entry.
    if c.kind in ATTACH_REQUIRED_KINDS:
        if not (c.install_requirements or c.attach_requirements):
            reasons.append(f"{c.kind} 인데 install/attach requirements 없음 — 부착 불가(fake available 방지)")
        else:
            evidence.append("install/attach requirements 명시")
        if c.kind in ("mcp", "plugin") and not c.provider_affinity:
            reasons.append(f"{c.kind} 인데 provider_affinity 없음 — 어느 harness 에 붙는지 불명")
        elif c.provider_affinity:
            evidence.append(f"provider_affinity={','.join(c.provider_affinity)}")

    return tuple(reasons), tuple(evidence)


def _to_spec(c: ArmoryCandidate) -> SkillSpec:
    return SkillSpec(
        id=c.id, name=c.name, domains=c.domains, languages=c.languages,
        frameworks=c.frameworks, topics=c.topics, rules=c.rules, commands=c.commands,
        verification=c.verification, forbidden=c.unsafe_boundary,
        related_weapons=c.related_weapons, related_loadouts=c.related_loadouts,
        related_roles=c.related_roles, nexus_refs=c.nexus_refs, category=c.category,
        summary=c.summary, when_to_use=c.when_to_use, when_not_to_use=c.when_not_to_use,
        required_inputs=c.required_inputs, expected_outputs=c.expected_outputs,
        signals=c.signals, capability_note=c.capability_note, status=c.status,
        kind=c.kind, provider_affinity=c.provider_affinity,
        install_requirements=c.install_requirements, attach_requirements=c.attach_requirements,
    )


def promote_candidate(c: ArmoryCandidate) -> PromotionResult:
    """Gate one candidate: validate its contract → accept (SkillSpec) or reject (reasons).

    No partial promotion — an incomplete contract is rejected wholesale so the catalog
    never gains a placeholder entry. The evidence trail records which gates passed.
    """

    reasons, evidence = _validate(c)
    if reasons:
        return PromotionResult(candidate_id=c.id, accepted=False, reasons=reasons, evidence=evidence)
    spec = _to_spec(c)
    src = f" (source={c.source}{':' + c.source_ref if c.source_ref else ''})"
    return PromotionResult(candidate_id=c.id, accepted=True, spec=spec,
                           evidence=evidence + (f"승격됨{src}",))


__all__ = ("ArmoryCandidate", "PromotionResult", "promote_candidate")
