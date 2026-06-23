"""ForgeKit 도입 효율 검토 — 외부 plugin/skill/collector/rule/workflow/tool 채택 게이트.

The wave-wide forcing rule: a candidate is **not** adopted because "좋아 보인다". Every
candidate carries a structured 8-point efficiency review, is reviewed on the **3 axes**
(PM · tech-lead · relevant specialist), and resolves to exactly one verdict —
``adopt-now`` / ``collect-first`` / ``hold``. Two states are kept distinct (the Hephaistos
distinction): **adopted** (the decision is made) vs **equipped** (it is actually
installed/wired). ``collect-first`` accrues evidence only; it is never equipped. No fake
adoption: equipping requires an ``adopt-now`` verdict that passed the 3-axis review.

This is the sibling of :mod:`.consult_gate` — consult asks "did a role weigh in before a
decision?"; this asks "is the *adoption decision itself* justified and on which footing?".
A valid review yields :func:`adoption_artifact_ref`, which can satisfy the consult merge
gate's ``design_refs`` for a dependency/abstraction change.

Pure — no I/O. Reviewer roles resolve through the identity registry SSoT.
Docs SSoT: ``docs/forgekit-integration-wave-qa.md`` (adoption forcing rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from forgekit_config.identity.registry import canonical_id

# --- verdicts ----------------------------------------------------------------
VERDICT_ADOPT_NOW = "adopt-now"        # decision: equip now (3-axis reviewed, justified)
VERDICT_COLLECT_FIRST = "collect-first"  # decision: accrue evidence in Nexus, do NOT equip
VERDICT_HOLD = "hold"                   # decision: not now (neither adopted nor equipped)
VERDICTS: Tuple[str, ...] = (VERDICT_ADOPT_NOW, VERDICT_COLLECT_FIRST, VERDICT_HOLD)

# candidate kinds the rule governs.
CANDIDATE_KINDS: Tuple[str, ...] = (
    "plugin", "skill", "collector", "rule", "workflow", "tool",
)

# the 3-axis review: PM + tech-lead are mandatory; ≥1 *additional* (specialist) role.
AXIS_PM = "product-manager"
AXIS_TECH_LEAD = "tech-lead"
MANDATORY_AXES: Tuple[str, ...] = (AXIS_PM, AXIS_TECH_LEAD)

# the 8 narrative fields a real review must fill (in report order).
REVIEW_FIELDS: Tuple[str, ...] = (
    "current_pain", "expected_benefit", "overlap_with_existing", "operational_cost",
    "maintenance_risk", "provider_runtime_fit", "governance_security_impact", "why_now",
)

_MIN_SUBSTANCE = 6   # a field shorter than this (stripped) is a placeholder, not a reason


@dataclass(frozen=True)
class ToolAdoptionReview:
    """One candidate's adoption-efficiency review. ``adopted``/``equipped`` are kept
    separate so a recorded decision can never masquerade as an installed capability."""

    candidate_id: str
    candidate_kind: str
    # the 8 efficiency points
    current_pain: str = ""
    expected_benefit: str = ""
    overlap_with_existing: str = ""
    operational_cost: str = ""
    maintenance_risk: str = ""
    provider_runtime_fit: str = ""
    governance_security_impact: str = ""
    why_now: str = ""                              # adopt-now vs collect-first vs hold 근거
    verdict: str = VERDICT_HOLD
    reviewers: Tuple[str, ...] = ()                # roles that reviewed (PM+tech-lead+≥1 specialist)
    adopted: bool = False                          # decision recorded (== verdict adopt-now)
    equipped: bool = False                         # ACTUALLY installed/wired (gate-only)
    evidence_refs: Tuple[str, ...] = ()            # Nexus note ids / decision refs

    def to_dict(self) -> dict:
        d = {f: getattr(self, f) for f in REVIEW_FIELDS}
        d.update(candidate_id=self.candidate_id, candidate_kind=self.candidate_kind,
                 verdict=self.verdict, reviewers=list(self.reviewers),
                 adopted=self.adopted, equipped=self.equipped,
                 evidence_refs=list(self.evidence_refs))
        return d


def _blank(s: str) -> bool:
    return not s or len(s.strip()) < _MIN_SUBSTANCE


def reviewer_axes(review: ToolAdoptionReview) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Resolve reviewers → (canonical roles present, the mandatory axes still missing)."""
    present = tuple(canonical_id(r) for r in review.reviewers if canonical_id(r))
    missing = tuple(a for a in MANDATORY_AXES if a not in present)
    return present, missing


def has_three_axis_review(review: ToolAdoptionReview) -> bool:
    """PM + tech-lead + at least one additional (specialist) role all reviewed."""
    present, missing = reviewer_axes(review)
    if missing:
        return False
    specialists = tuple(r for r in present if r not in MANDATORY_AXES)
    return len(specialists) >= 1


def validate_adoption_review(review: ToolAdoptionReview) -> Tuple[str, ...]:
    """Reject a fake/under-justified review. ``()`` = valid.

    A real review: a known candidate kind, all 8 efficiency points substantive, a legal
    verdict, and — for ``adopt-now`` — a full 3-axis review. ``adopted`` must mirror the
    verdict, and ``equipped`` may only be True on an adopted (adopt-now) review. So
    ``collect-first``/``hold`` can never be silently equipped (no fake adoption)."""

    v = []
    if not review.candidate_id or not review.candidate_id.strip():
        v.append("adoption: candidate_id 비어 있음")
    if review.candidate_kind not in CANDIDATE_KINDS:
        v.append(f"adoption: candidate_kind '{review.candidate_kind}' 미지원 "
                 f"(허용: {', '.join(CANDIDATE_KINDS)})")
    for f in REVIEW_FIELDS:
        if _blank(getattr(review, f)):
            v.append(f"adoption: {f} 비어 있음/placeholder — 8점 검토 필수")
    if review.verdict not in VERDICTS:
        v.append(f"adoption: verdict '{review.verdict}' 미지원 (허용: {', '.join(VERDICTS)})")

    # reviewer roles resolve through the registry
    for r in review.reviewers:
        if not canonical_id(r):
            v.append(f"adoption: reviewer '{r}' 이 식별자 레지스트리에 없음")

    # adopted must mirror the verdict; only adopt-now is an adoption decision
    if review.adopted != (review.verdict == VERDICT_ADOPT_NOW):
        v.append("adoption: adopted 플래그가 verdict 와 불일치 "
                 "(adopt-now 만 adopted=True)")

    # adopt-now requires the full 3-axis review
    if review.verdict == VERDICT_ADOPT_NOW and not has_three_axis_review(review):
        _, missing = reviewer_axes(review)
        if missing:
            v.append(f"adoption: adopt-now 인데 3축 검토 미충족 — 누락 축: {list(missing)}")
        else:
            v.append("adoption: adopt-now 인데 3축 미충족 — specialist(PM/tech-lead 외) 검토 1개 이상 필요")

    # no fake adoption: equipped only on an adopted (adopt-now) review
    if review.equipped and not (review.adopted and review.verdict == VERDICT_ADOPT_NOW):
        v.append("adoption: equipped=True 인데 adopt-now/ adopted 아님 — fake adoption 금지")

    # collect-first/hold must not be equipped
    if review.verdict in (VERDICT_COLLECT_FIRST, VERDICT_HOLD) and review.equipped:
        v.append(f"adoption: {review.verdict} 후보는 장착(equip) 금지 — 근거만 누적")

    return tuple(v)


def can_equip(review: ToolAdoptionReview) -> bool:
    """A candidate may be EQUIPPED (actually installed/wired) only when its review is
    valid, the verdict is ``adopt-now``, and the 3-axis review passed. ``collect-first``
    and ``hold`` can never be equipped — the gate that prevents fake adoption."""
    return (not validate_adoption_review(review)
            and review.verdict == VERDICT_ADOPT_NOW
            and review.adopted
            and has_three_axis_review(review))


def adoption_artifact_ref(review: ToolAdoptionReview) -> str:
    """A stable id for a *valid* review, usable as a consult-gate ``design_refs`` entry
    (a dependency/abstraction change with this ref carries its adoption rationale).
    ``""`` for an invalid review (so a fake review cannot satisfy the consult gate)."""
    if validate_adoption_review(review):
        return ""
    return f"adoption:{review.candidate_id}:{review.verdict}"


@dataclass(frozen=True)
class AdoptionReviewReport:
    """Wave roll-up: split candidates by verdict + flag any fake adoption (a review that
    fails validation, e.g. equipped without adopt-now)."""

    reviews: Tuple[ToolAdoptionReview, ...] = ()

    def _by(self, verdict: str) -> Tuple[ToolAdoptionReview, ...]:
        return tuple(r for r in self.reviews if r.verdict == verdict)

    @property
    def adopt_now(self) -> Tuple[ToolAdoptionReview, ...]:
        return self._by(VERDICT_ADOPT_NOW)

    @property
    def collect_first(self) -> Tuple[ToolAdoptionReview, ...]:
        return self._by(VERDICT_COLLECT_FIRST)

    @property
    def hold(self) -> Tuple[ToolAdoptionReview, ...]:
        return self._by(VERDICT_HOLD)

    @property
    def equipped(self) -> Tuple[ToolAdoptionReview, ...]:
        return tuple(r for r in self.reviews if r.equipped)

    @property
    def invalid(self) -> Tuple[ToolAdoptionReview, ...]:
        """Reviews that fail validation — includes any fake adoption (equipped without
        a passing adopt-now). A non-empty list blocks the wave."""
        return tuple(r for r in self.reviews if validate_adoption_review(r))

    @property
    def fake_adoption_blocked(self) -> bool:
        return bool(self.invalid)

    def to_dict(self) -> dict:
        return {"adopt_now": [r.candidate_id for r in self.adopt_now],
                "collect_first": [r.candidate_id for r in self.collect_first],
                "hold": [r.candidate_id for r in self.hold],
                "equipped": [r.candidate_id for r in self.equipped],
                "invalid": [r.candidate_id for r in self.invalid],
                "fake_adoption_blocked": self.fake_adoption_blocked}

    def lines(self) -> Tuple[str, ...]:
        out = [f"adoption review — {len(self.reviews)} candidate, "
               + ("FAKE ADOPTION BLOCKED" if self.fake_adoption_blocked else "정직")]
        out.append(f"  adopt-now={len(self.adopt_now)} collect-first={len(self.collect_first)} "
                   f"hold={len(self.hold)} · equipped={len(self.equipped)} invalid={len(self.invalid)}")
        for r in self.reviews:
            eq = " ⚙equipped" if r.equipped else ""
            bad = " ✗invalid" if validate_adoption_review(r) else ""
            out.append(f"  · {r.candidate_id} [{r.candidate_kind}] → {r.verdict}{eq}{bad}")
        return tuple(out)


def adoption_review_report(reviews: Sequence[ToolAdoptionReview]) -> AdoptionReviewReport:
    return AdoptionReviewReport(tuple(reviews))


__all__ = (
    "VERDICT_ADOPT_NOW", "VERDICT_COLLECT_FIRST", "VERDICT_HOLD", "VERDICTS",
    "CANDIDATE_KINDS", "MANDATORY_AXES", "AXIS_PM", "AXIS_TECH_LEAD", "REVIEW_FIELDS",
    "ToolAdoptionReview", "AdoptionReviewReport",
    "validate_adoption_review", "has_three_axis_review", "reviewer_axes",
    "can_equip", "adoption_artifact_ref", "adoption_review_report",
)
