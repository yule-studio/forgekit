"""armory.adoption — ForgeKit 도입 효율 검토 (intake 후보의 adopt/collect/hold 결정).

An ``AdoptionReview`` is the **strategic** evaluation that precedes catalog promotion:
"좋아 보인다" 만으로 외부 plugin/skill/tool 을 들이지 않고, ForgeKit 관점의 8축 효율을
명시한 뒤 PM / tech-lead / specialist 3축 검토를 거쳐 **adopt-now / collect-first / hold**
중 하나로 귀결시킨다. This is distinct from ``armory.candidate`` (which validates the
*catalog contract* of an already-decided entry) — adoption decides *whether* to bring a
thing in at all, and at what speed.

Honesty rails:
- **adopted ≠ equipped/installed.** adopt-now 는 "카탈로그에 올린다(available)" 이지
  설치/장착이 아니다. attach 류(tool/plugin/mcp)는 ``install_plan`` 으로 설치 경로만
  *선언*한다 — install_plan 없는 attach 류는 adopt-now 될 수 없다(fake available 방지).
- **collect-first** 는 Nexus 에 근거만 누적하고 즉시 활성화하지 않는다.
- 3축(PM/tech-lead/specialist) 검토가 모두 있어야 하고, adopt-now 는 3축 합의 필요.

Pure / stdlib-only — armory 는 leaf 라 decision_lane(forgekit-runtime)을 import 하지
않는다. 실제 council 배선이 필요하면 console 합성층이 한다(see docs/armory-intake-adoption.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from .models import ATTACH_REQUIRED_KINDS, ENTRY_KINDS

# adoption verdict — the three terminal buckets every candidate resolves into.
VERDICT_ADOPT_NOW = "adopt-now"
VERDICT_COLLECT_FIRST = "collect-first"
VERDICT_HOLD = "hold"
ADOPTION_VERDICTS = (VERDICT_ADOPT_NOW, VERDICT_COLLECT_FIRST, VERDICT_HOLD)

# the mandatory review axes (최소 3축).
AXIS_PM = "pm"
AXIS_TECH_LEAD = "tech-lead"
AXIS_SPECIALIST = "specialist"
REQUIRED_AXES = (AXIS_PM, AXIS_TECH_LEAD, AXIS_SPECIALIST)

# placeholder tokens that disqualify a "filled" artifact field.
_PLACEHOLDERS = ("tbd", "todo", "fixme", "xxx", "placeholder", "...", "내용 없음", "(미정)", "?")
_MIN_LEN = 8   # an artifact field must be a real sentence, not "ok"


def _is_thin(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) < _MIN_LEN:
        return True
    return any(p in t for p in _PLACEHOLDERS)


@dataclass(frozen=True)
class ReviewerVerdict:
    """One review axis's recommendation + rationale (the 3축 검토 artifact)."""

    axis: str          # AXIS_*
    verdict: str       # ADOPTION_VERDICTS (that axis's recommendation)
    rationale: str

    def to_dict(self) -> dict:
        return {"axis": self.axis, "verdict": self.verdict, "rationale": self.rationale}


@dataclass(frozen=True)
class AdoptionReview:
    """A ForgeKit 도입 효율 검토 for one external candidate — 8축 + 3축 검토 + verdict."""

    candidate_id: str
    name: str
    kind: str            # ENTRY_KINDS: skill / tool / plugin / mcp
    source: str          # repo / homepage URL
    # --- the 8 ForgeKit-adoption-efficiency fields (wave-mandated artifact) ---
    current_pain: str            # 지금 무엇이 아픈가
    expected_benefit: str        # 도입 시 기대 효과
    overlap: str                 # 기존 Armory/Nexus/Hephaistos capability 와 겹침
    operational_cost: str        # install/runtime burden
    maintenance_risk: str        # 유지보수 리스크
    provider_runtime_fit: str    # provider/runtime 적합성
    governance_security: str     # governance/security 영향
    verdict: str                 # ADOPTION_VERDICTS — adopt-now/collect-first/hold
    # --- review + routing ---
    reviewers: Tuple[ReviewerVerdict, ...] = ()
    install_plan: Tuple[str, ...] = ()   # attach 류의 설치/장착 경로 *선언* (실행 아님)
    loadout_id: str = ""                 # adopt-now 가 카탈로그 loadout 으로 실현됐으면 그 id
    notes: str = ""

    @property
    def is_attach_kind(self) -> bool:
        return self.kind in ATTACH_REQUIRED_KINDS

    def axis(self, axis: str):
        return next((r for r in self.reviewers if r.axis == axis), None)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "name": self.name, "kind": self.kind,
            "source": self.source,
            "artifact": {
                "current_pain": self.current_pain, "expected_benefit": self.expected_benefit,
                "overlap": self.overlap, "operational_cost": self.operational_cost,
                "maintenance_risk": self.maintenance_risk,
                "provider_runtime_fit": self.provider_runtime_fit,
                "governance_security": self.governance_security,
            },
            "verdict": self.verdict,
            "reviewers": [r.to_dict() for r in self.reviewers],
            "install_plan": list(self.install_plan),
            "loadout_id": self.loadout_id, "notes": self.notes,
        }


def validate_review(r: AdoptionReview) -> Tuple[str, ...]:
    """Return reasons the review is INVALID (empty tuple = valid). Deterministic.

    Enforces the wave's honesty rails: every artifact field filled, 3축 검토 present,
    a legal verdict, and the adopt-now consensus + install-plan gates (fake adoption 방지).
    """

    reasons = []
    if not r.candidate_id.strip() or not r.name.strip():
        reasons.append("candidate_id/name 없음")
    if r.kind not in ENTRY_KINDS:
        reasons.append(f"kind 불명: {r.kind!r} (skill/tool/plugin/mcp)")
    if not r.source.strip():
        reasons.append("source(repo/url) 없음")

    # all 8 artifact fields must be real (not placeholder/too short).
    for fname in ("current_pain", "expected_benefit", "overlap", "operational_cost",
                  "maintenance_risk", "provider_runtime_fit", "governance_security"):
        if _is_thin(getattr(r, fname)):
            reasons.append(f"{fname} 가 비었거나 placeholder")

    if r.verdict not in ADOPTION_VERDICTS:
        reasons.append(f"verdict 불명: {r.verdict!r}")

    # 3축 검토 mandatory.
    axes = {rv.axis for rv in r.reviewers}
    missing = [a for a in REQUIRED_AXES if a not in axes]
    if missing:
        reasons.append(f"검토 축 누락: {', '.join(missing)} (PM/tech-lead/specialist 필수)")
    for rv in r.reviewers:
        if rv.verdict not in ADOPTION_VERDICTS:
            reasons.append(f"{rv.axis} verdict 불명: {rv.verdict!r}")
        if _is_thin(rv.rationale):
            reasons.append(f"{rv.axis} rationale 비었음")

    # adopt-now gates — consensus + (attach 류) install plan.
    if r.verdict == VERDICT_ADOPT_NOW:
        axis_verdicts = [rv.verdict for rv in r.reviewers if rv.axis in REQUIRED_AXES]
        if any(v != VERDICT_ADOPT_NOW for v in axis_verdicts):
            reasons.append("adopt-now 인데 3축 합의 아님 (한 축이라도 collect-first/hold)")
        if r.is_attach_kind and not r.install_plan:
            reasons.append(
                f"{r.kind} adopt-now 인데 install_plan 없음 — 설치 경로 미선언(fake available 방지)")

    return tuple(reasons)


def by_verdict(reviews: Sequence[AdoptionReview], verdict: str) -> Tuple[AdoptionReview, ...]:
    return tuple(r for r in reviews if r.verdict == verdict)


def invalid_reviews(reviews: Sequence[AdoptionReview]) -> Tuple[Tuple[AdoptionReview, Tuple[str, ...]], ...]:
    """Every review that fails validation, paired with its reasons (audit surface)."""

    out = []
    for r in reviews:
        bad = validate_review(r)
        if bad:
            out.append((r, bad))
    return tuple(out)


__all__ = (
    "VERDICT_ADOPT_NOW", "VERDICT_COLLECT_FIRST", "VERDICT_HOLD", "ADOPTION_VERDICTS",
    "AXIS_PM", "AXIS_TECH_LEAD", "AXIS_SPECIALIST", "REQUIRED_AXES",
    "ReviewerVerdict", "AdoptionReview", "validate_review", "by_verdict", "invalid_reviews",
)
