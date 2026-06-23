"""Adoption review — the "ForgeKit 도입 효율 검토" gate for external candidates.

An external plugin / skill / collector / rule / workflow is NEVER adopted on "looks
good". This is the design-decision artifact that forces a real adoption review BEFORE a
candidate is wired in: the eight mandated review fields (current pain → governance impact),
a minimum 3-axis review (proposer + PM + tech-lead + ≥1 specialist), a ponytail (lean)
verdict, and exactly one adoption verdict:

* ``adopt-now``     — review passed, adopt and proceed to equipping (activation).
* ``collect-first`` — promising but unproven → accumulate evidence in Nexus ONLY, do NOT
  activate. (the "collect-first 후보는 근거만 누적" rule.)
* ``hold``          — not now (overlap / cost / risk) — kept as a recorded decision.

The **"adopted" ≠ "equipped"** split (the Hephaistos distinction) lives in
:func:`can_equip`: only a VALID ``adopt-now`` review may proceed to equipping
(activation). A ``collect-first`` / ``hold`` review can never gate an install — so a fake
"adopted" can never become a silently-equipped capability.

Anti-fake (:func:`validate_adoption_review`): all eight fields must be substantive, the
3-axis review must name a real PM (canonical ``product-manager``) and tech-lead, and an
``adopt-now`` verdict additionally requires a follow-up owner + verification + an assessed
governance/security impact. Pure / stdlib-only; roles resolve through the registry SSoT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from forgekit_config.identity.registry import canonical_id, resolve_identity

from .schemas import RejectedOption

# adoption verdicts (the only three a candidate may resolve to)
ADOPT_NOW = "adopt-now"
COLLECT_FIRST = "collect-first"
HOLD = "hold"
ADOPTION_VERDICTS: Tuple[str, ...] = (ADOPT_NOW, COLLECT_FIRST, HOLD)

# candidate kinds the review covers (informational — not gating)
CANDIDATE_KINDS: Tuple[str, ...] = (
    "plugin", "skill", "collector", "rule", "workflow", "tool")


def _blank(s: str) -> bool:
    return not (s or "").strip()


@dataclass(frozen=True)
class AdoptionReview:
    """A ForgeKit 도입 효율 검토 for one external candidate + its 3-axis review verdict."""

    candidate_id: str
    candidate_kind: str = "plugin"
    # --- the 8 mandated review fields ---------------------------------------
    current_pain: str = ""                  # 1. 지금 어떤 pain 을 해결하나
    expected_benefit: str = ""              # 2. 도입 시 기대 benefit
    overlap_with_existing: str = ""         # 3. 기존 capability 와 중복
    operational_cost: str = ""              # 4. 운영 비용
    maintenance_risk: str = ""              # 5. 유지보수 리스크
    provider_runtime_fit: str = ""          # 6. provider/runtime 적합성
    governance_security_impact: str = ""    # 7. governance/security 영향
    why_adopt_now: str = ""                 # 8. why adopt-now vs collect-first vs hold
    # --- 3-axis review meta --------------------------------------------------
    proposed_by: str = ""                   # 누가 제안했나
    reviewed_by_pm: str = ""                # PM 검토자 (canonical product-manager)
    reviewed_by_tech_lead: str = ""         # tech-lead 검토자 (canonical tech-lead)
    specialist_consulted: Tuple[str, ...] = ()   # ≥1 specialist (engineering role)
    ponytail_verdict: str = ""              # 최소-구조(lean) 검토 — wrapper/indirection 과다?
    adoption_verdict: str = ""              # adopt-now / collect-first / hold
    follow_up_owner: str = ""               # 후속 owner
    # --- supporting evidence -------------------------------------------------
    rejected_alternatives: Tuple[RejectedOption, ...] = ()
    constraints: Tuple[str, ...] = ()
    verification: Tuple[str, ...] = ()      # adopt-now 시 검증 방법 (≥1)
    nexus_evidence_ref: str = ""            # collect-first 시 근거 누적 위치

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "candidate_kind": self.candidate_kind,
            "current_pain": self.current_pain, "expected_benefit": self.expected_benefit,
            "overlap_with_existing": self.overlap_with_existing,
            "operational_cost": self.operational_cost, "maintenance_risk": self.maintenance_risk,
            "provider_runtime_fit": self.provider_runtime_fit,
            "governance_security_impact": self.governance_security_impact,
            "why_adopt_now": self.why_adopt_now, "proposed_by": self.proposed_by,
            "reviewed_by_pm": self.reviewed_by_pm,
            "reviewed_by_tech_lead": self.reviewed_by_tech_lead,
            "specialist_consulted": list(self.specialist_consulted),
            "ponytail_verdict": self.ponytail_verdict, "adoption_verdict": self.adoption_verdict,
            "follow_up_owner": self.follow_up_owner,
            "rejected_alternatives": [r.to_dict() for r in self.rejected_alternatives],
            "constraints": list(self.constraints), "verification": list(self.verification),
            "nexus_evidence_ref": self.nexus_evidence_ref,
        }

    def lines(self) -> Tuple[str, ...]:
        out = [
            f"adoption review — {self.candidate_kind}:{self.candidate_id} → {self.adoption_verdict or '-'}",
            f"  pain     : {self.current_pain}",
            f"  benefit  : {self.expected_benefit}",
            f"  overlap  : {self.overlap_with_existing}",
            f"  cost/risk: {self.operational_cost} / {self.maintenance_risk}",
            f"  fit      : {self.provider_runtime_fit}",
            f"  gov/sec  : {self.governance_security_impact}",
            f"  why-now  : {self.why_adopt_now}",
            f"  review   : 제안={self.proposed_by} pm={self.reviewed_by_pm} "
            f"tech-lead={self.reviewed_by_tech_lead} specialist=[{', '.join(self.specialist_consulted)}]",
            f"  ponytail : {self.ponytail_verdict}",
            f"  follow-up: {self.follow_up_owner or '-'}",
        ]
        return tuple(out)


# the 8 mandated fields, paired with their human label (for one-pass validation).
_REQUIRED_FIELDS = (
    ("current_pain", "1.current pain"),
    ("expected_benefit", "2.expected benefit"),
    ("overlap_with_existing", "3.overlap"),
    ("operational_cost", "4.operational cost"),
    ("maintenance_risk", "5.maintenance risk"),
    ("provider_runtime_fit", "6.provider/runtime fit"),
    ("governance_security_impact", "7.governance/security impact"),
    ("why_adopt_now", "8.why adopt-now"),
)


def validate_adoption_review(review: AdoptionReview) -> Tuple[str, ...]:
    """Reject a fake/thin adoption review. ``()`` = a real 3-axis review the lane honors."""

    v = []
    if _blank(review.candidate_id):
        v.append("adoption: candidate_id 비어 있음")

    # the 8 mandated review fields must all be substantive.
    for attr, label in _REQUIRED_FIELDS:
        if _blank(getattr(review, attr, "")):
            v.append(f"adoption: {label} 비어 있음 — 도입 효율 검토 미완")

    # 3-axis review — proposer + PM + tech-lead + ≥1 specialist, each registry-resolved.
    if _blank(review.proposed_by) or not canonical_id(review.proposed_by):
        v.append("adoption: proposed_by 없음/레지스트리 미등록")
    if canonical_id(review.reviewed_by_pm) != "product-manager":
        v.append("adoption: reviewed_by_pm 가 product-manager 아님 — PM 검토 축 누락")
    if canonical_id(review.reviewed_by_tech_lead) != "tech-lead":
        v.append("adoption: reviewed_by_tech_lead 가 tech-lead 아님 — tech-lead 검토 축 누락")
    specialists = [s for s in review.specialist_consulted if canonical_id(s)
                   and resolve_identity(canonical_id(s)).department == "engineering"]
    if not specialists:
        v.append("adoption: specialist_consulted 최소 1 (engineering) 필요 — 3축 검토 미충족")

    if _blank(review.ponytail_verdict):
        v.append("adoption: ponytail_verdict 비어 있음 — 최소-구조 검토 누락")

    # exactly one of the three verdicts.
    if review.adoption_verdict not in ADOPTION_VERDICTS:
        v.append(f"adoption: adoption_verdict '{review.adoption_verdict}' 은 "
                 f"{ADOPTION_VERDICTS} 중 하나여야 함")
        return tuple(v)

    # verdict-specific obligations.
    if review.adoption_verdict == ADOPT_NOW:
        if _blank(review.follow_up_owner):
            v.append("adoption: adopt-now 인데 follow_up_owner 없음")
        if not review.verification:
            v.append("adoption: adopt-now 인데 verification 없음 — 검증 없이 도입 금지")
    elif review.adoption_verdict == COLLECT_FIRST:
        if _blank(review.nexus_evidence_ref):
            v.append("adoption: collect-first 인데 nexus_evidence_ref 없음 — 근거 누적처 없음")
    return tuple(v)


def can_equip(review: AdoptionReview) -> bool:
    """The "adopted ≠ equipped" gate: only a VALID ``adopt-now`` review may proceed to
    equipping (the install/activation lane). collect-first / hold → never equips."""

    return (not validate_adoption_review(review)) and review.adoption_verdict == ADOPT_NOW


def equip_block_reason(review: AdoptionReview) -> str:
    """Why a candidate may NOT be equipped (empty string = it may). Honest surface text."""

    if can_equip(review):
        return ""
    viols = validate_adoption_review(review)
    if viols:
        return f"adoption review 미통과: {viols[0]}"
    if review.adoption_verdict == COLLECT_FIRST:
        return "collect-first — 근거만 Nexus 누적, 아직 장착 금지"
    if review.adoption_verdict == HOLD:
        return "hold — 도입 보류"
    return "adopt-now 아님 — 장착 불가"


__all__ = (
    "ADOPT_NOW", "COLLECT_FIRST", "HOLD", "ADOPTION_VERDICTS", "CANDIDATE_KINDS",
    "AdoptionReview", "validate_adoption_review", "can_equip", "equip_block_reason",
)
