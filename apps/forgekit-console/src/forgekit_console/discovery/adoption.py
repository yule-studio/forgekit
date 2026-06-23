"""Adoption-efficiency review — turn a collected candidate into a *도입 가치 판단* artifact.

Collecting is not the goal; producing evidence good enough to DECIDE adoption is. This
module is that decision layer. A discovery candidate (idea / tool / competitor signal /
reference) is classified, then run through an **efficiency review** that answers the 8
questions an operator needs before adopting anything external:

  1. current pain          2. expected benefit       3. overlap with existing capability
  4. operational cost      5. maintenance risk        6. provider/runtime fit
  7. governance/security    8. why adopt-now vs collect-first vs hold

The review never *fakes* adoption. It defaults to **collect-first** (accumulate evidence,
do not activate) and emits a real :class:`ConsultNote` requesting the mandated 3-axis
(PM / tech-lead / specialist) review. Only an explicit operator decision after that review
flips it to **adopt-now**, which bridges to the armory intake gate (``promote_candidate``)
— and even then *adopted* (a validated catalog spec) is distinct from *equipped*
(``catalog.register_promoted``), which stays a separate, explicit step.

ponytail verdict: a NEW module is warranted — this is the genuinely new adoption-decision
layer that bridges discovery ↔ armory ↔ decision_lane; it is not a wrapper over any one of
them. Classification lives here (not in pipeline._classify, which tags opportunity *signals*)
because it is specific to adoption candidacy. armory / decision_lane are imported lazily so
this stays a thin, dependency-light bridge, not a service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from . import models as M

# candidate classification (what KIND of thing did we collect?) ----------------
CLASS_SIGNAL_ONLY = "signal_only"
CLASS_TOOL = "tool_candidate"
CLASS_IDEA = "idea_candidate"
CLASS_COMPETITOR = "competitor_signal"
CLASS_IMPL_REF = "implementation_reference"
CLASS_RISK = "risk_or_constraint"

# disposition (the only three outcomes a candidate can reach) -------------------
ADOPT_NOW = "adopt-now"
COLLECT_FIRST = "collect-first"
HOLD = "hold"

# keyword cues — order matters: risk/competitor checked before generic tool/idea.
_RISK = ("위험", "제약", "라이선스", "license", "보안", "security", "취약", "vuln",
         "deprecated", "eol", "tos", "쿼터", "quota", "유료", "비용 폭증")
_COMPETITOR = ("경쟁", "competitor", "alternative", "대체", " vs ", "rival", "경쟁사")
_TOOL = ("도구", "tool", "라이브러리", "library", "sdk", " cli", "framework", "프레임워크",
         "플러그인", "plugin", "mcp", "extension")
_IMPL_REF = ("구현", "예제", "example", "튜토리얼", "tutorial", "github.com", "snippet",
             "패턴", "pattern", "how to", "레퍼런스", "reference")
_IDEA = ("아이디어", "idea", "기능", "feature", "제품", "product", "서비스", "service")

# which specialist the 3-axis review pulls in, by classification.
_SPECIALIST = {
    CLASS_TOOL: "platform-runtime-engineer",
    CLASS_IDEA: "backend-engineer",
    CLASS_COMPETITOR: "growth-analyst",
    CLASS_IMPL_REF: "backend-engineer",
    CLASS_RISK: "security-engineer",
    CLASS_SIGNAL_ONLY: "user-researcher",
}


def classify_candidate(text: str) -> str:
    """Classify a collected item into one of the six adoption-candidate classes."""

    low = f" {(text or '').lower()} "
    if any(k in low for k in _RISK):
        return CLASS_RISK
    if any(k in low for k in _COMPETITOR):
        return CLASS_COMPETITOR
    if any(k in low for k in _TOOL):
        return CLASS_TOOL
    if any(k in low for k in _IMPL_REF):
        return CLASS_IMPL_REF
    if any(k in low for k in _IDEA):
        return CLASS_IDEA
    return CLASS_SIGNAL_ONLY


def _overlap(text: str, existing_signals: Sequence[str]) -> Tuple[bool, str]:
    """Token overlap with an existing-capability signal set (cheap, deterministic)."""

    toks = {t for t in (text or "").lower().split() if len(t) >= 4}
    for sig in existing_signals:
        s = (sig or "").lower()
        if s and (s in (text or "").lower() or any(t in s for t in toks)):
            return True, sig
    return False, ""


@dataclass(frozen=True)
class AdoptionReview:
    """The 8-field efficiency review for one candidate + its disposition + 3-axis consult."""

    candidate_id: str
    title: str
    classification: str
    source_id: str = ""
    score: float = 0.0
    # the 8 efficiency-review fields ------------------------------------------
    current_pain: str = ""
    expected_benefit: str = ""
    overlap: str = ""
    operational_cost: str = ""
    maintenance_risk: str = ""
    provider_runtime_fit: str = ""
    governance_security_impact: str = ""
    disposition_rationale: str = ""
    # outcome ------------------------------------------------------------------
    disposition: str = COLLECT_FIRST
    reviewed: bool = False                 # True only after the 3-axis review resolved it
    consult: dict = field(default_factory=dict)   # ConsultNote.to_dict() — the review request

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "title": self.title,
            "classification": self.classification, "source_id": self.source_id,
            "score": self.score, "current_pain": self.current_pain,
            "expected_benefit": self.expected_benefit, "overlap": self.overlap,
            "operational_cost": self.operational_cost, "maintenance_risk": self.maintenance_risk,
            "provider_runtime_fit": self.provider_runtime_fit,
            "governance_security_impact": self.governance_security_impact,
            "disposition_rationale": self.disposition_rationale,
            "disposition": self.disposition, "reviewed": self.reviewed,
            "consult": self.consult,
        }

    def lines(self) -> Tuple[str, ...]:
        return (
            f"도입 효율 검토 — {self.title}",
            f"- 분류: {self.classification} · 출처 {self.source_id or 'operator'} · score {self.score}",
            f"- 1 current pain: {self.current_pain}",
            f"- 2 expected benefit: {self.expected_benefit}",
            f"- 3 overlap: {self.overlap}",
            f"- 4 operational cost: {self.operational_cost}",
            f"- 5 maintenance risk: {self.maintenance_risk}",
            f"- 6 provider/runtime fit: {self.provider_runtime_fit}",
            f"- 7 governance/security: {self.governance_security_impact}",
            f"- 8 disposition: {self.disposition} — {self.disposition_rationale}",
            f"- 3축 검토 요청: {', '.join(self.consult.get('to_roles', ())) or '(없음)'}"
            f"{' · 검토 완료' if self.reviewed else ' · 검토 대기'}",
        )


def _build_consult(candidate_id: str, title: str, classification: str) -> dict:
    """A REAL ConsultNote requesting the mandated 3-axis (PM/tech-lead/specialist) review."""

    from forgekit_runtime.decision_lane import ConsultNote

    specialist = _SPECIALIST.get(classification, "user-researcher")
    to_roles = tuple(dict.fromkeys(("product-manager", "tech-lead", specialist)))
    note = ConsultNote(
        consult_id=f"adopt-{candidate_id}",
        topic=f"도입 효율 검토: {title[:48]}",
        by_role="user-researcher",
        to_roles=to_roles,
        question=("이 후보를 adopt-now / collect-first / hold 중 무엇으로 둘지 — "
                  "pain·benefit·overlap·cost·risk·fit·governance 기준으로 검토 요청"),
        refs=(candidate_id,))
    return note.to_dict()


def build_adoption_review(brief: M.IdeaBrief, *, source_id: str = "",
                          existing_signals: Sequence[str] = ()) -> AdoptionReview:
    """Compute the 8-field efficiency review for a brief. Defaults to collect-first.

    Honest by construction: it never returns adopt-now (adoption requires the 3-axis review
    + an explicit operator decision via :func:`resolve_review`). A risk/constraint candidate
    or one that overlaps an existing capability defaults to **hold**; everything else to
    **collect-first** (accumulate evidence, do not activate)."""

    # classify on the candidate's OWN words (title/problem), not the boilerplate
    # differentiation hypothesis (which always says "기존 대체재보다…" → false competitor hits).
    text = f"{brief.title} {brief.problem}"
    classification = classify_candidate(text)
    has_overlap, overlap_with = _overlap(text, existing_signals)

    pain = brief.problem or brief.title
    benefit = brief.differentiation.hypothesis or f"'{brief.title}' 의 문제 해소"
    overlap = (f"기존 capability와 겹침: {overlap_with}" if has_overlap
               else "기존 capability와 겹침 관측 안 됨 (도입 시 별도 검증 필요)")
    cost = {
        CLASS_TOOL: "설치·attach·버전 유지 비용 (toolchain 영향)",
        CLASS_IDEA: "구현·검증 비용 (PM→tech-lead→specialist 경로)",
        CLASS_IMPL_REF: "낮음 — 참고 자료, 직접 도입 아님",
        CLASS_COMPETITOR: "낮음 — 관측/분석, 직접 도입 아님",
        CLASS_RISK: "낮음(직접 비용) — 추적·완화 비용",
        CLASS_SIGNAL_ONLY: "불명 — 아직 raw 신호",
    }.get(classification, "불명")
    risk = {
        CLASS_TOOL: "외부 의존·ToS·유지보수 리스크 (vendor lock-in 점검)",
        CLASS_RISK: "이 항목 자체가 리스크/제약 — 추적 대상",
        CLASS_SIGNAL_ONLY: "낮음 — 신호 누적 단계",
    }.get(classification, "보통 — 도입 전 검증 필요")
    fit = "provider-neutral 로 평가 (특정 vendor 가정 금지) — runtime/harness fit 은 specialist 검토"
    gov = ("governance/security 영향 큼 — security-engineer 검토 필수"
           if classification == CLASS_RISK else "표준 승인 게이트 적용 (실행은 게이트 통과 후)")

    if classification == CLASS_RISK:
        disposition, why = HOLD, "리스크/제약 항목 — 도입이 아니라 추적/완화 대상"
    elif has_overlap:
        disposition, why = HOLD, f"기존 capability({overlap_with})와 겹침 — 중복 도입 보류"
    else:
        disposition, why = COLLECT_FIRST, ("근거는 충분치 않음 — 3축 검토 전까지 evidence 만 누적"
                                           "(즉시 활성화 안 함)")

    cid = _slug_id(brief.problem or brief.title)
    return AdoptionReview(
        candidate_id=cid, title=brief.title, classification=classification,
        source_id=source_id, score=brief.score, current_pain=pain, expected_benefit=benefit,
        overlap=overlap, operational_cost=cost, maintenance_risk=risk,
        provider_runtime_fit=fit, governance_security_impact=gov,
        disposition_rationale=why, disposition=disposition, reviewed=False,
        consult=_build_consult(cid, brief.title, classification))


def resolve_review(review: AdoptionReview, *, adopt: bool, note: str = "") -> AdoptionReview:
    """Apply the operator's post-3-axis decision. adopt=True → adopt-now (else hold).

    This is the ONLY path to adopt-now — it records that the 3-axis review happened and the
    operator decided. A risk/constraint candidate can never be flipped to adopt-now here."""

    if adopt and review.classification == CLASS_RISK:
        adopt = False  # risk/constraint items are tracked, never adopted (honest guard)
    disposition = ADOPT_NOW if adopt else HOLD
    why = (f"3축 검토 후 operator 채택 결정 — {note}".rstrip(" —") if adopt
           else f"3축 검토 후 보류 — {note}".rstrip(" —"))
    consult = dict(review.consult)
    consult["note"] = note or ("adopt-now 결정" if adopt else "hold 결정")
    return AdoptionReview(
        candidate_id=review.candidate_id, title=review.title,
        classification=review.classification, source_id=review.source_id, score=review.score,
        current_pain=review.current_pain, expected_benefit=review.expected_benefit,
        overlap=review.overlap, operational_cost=review.operational_cost,
        maintenance_risk=review.maintenance_risk,
        provider_runtime_fit=review.provider_runtime_fit,
        governance_security_impact=review.governance_security_impact,
        disposition_rationale=why, disposition=disposition, reviewed=True, consult=consult)


def adoption_to_armory_candidate(review: AdoptionReview, *, contract: dict):
    """Bridge an adopt-now review to the armory intake gate → PromotionResult (ADOPTED).

    Only valid when the review is adopt-now AND reviewed. Builds an ``ArmoryCandidate`` from
    the review + an operator/specialist-supplied *contract* (summary/signals/when_to_use/
    unsafe_boundary/capability_note/commands — the fields a raw idea lacks) and runs
    ``promote_candidate``. Returns the ``PromotionResult``: accepted = a validated catalog
    spec (**adopted**). It does NOT register the spec — *equipped* (``register_promoted``)
    stays a separate, explicit step. Returns ``None`` if the review isn't adopt-now."""

    if not (review.disposition == ADOPT_NOW and review.reviewed):
        return None
    from armory.candidate import ArmoryCandidate, promote_candidate

    cand = ArmoryCandidate(
        id=review.candidate_id, name=review.title,
        kind=contract.get("kind", "skill"), category=contract.get("category", "discovery"),
        summary=contract.get("summary", "") or review.expected_benefit,
        signals=tuple(contract.get("signals", ()) or ()),
        when_to_use=tuple(contract.get("when_to_use", ()) or ()),
        unsafe_boundary=tuple(contract.get("unsafe_boundary", ()) or ()),
        capability_note=contract.get("capability_note", ""),
        provider_affinity=tuple(contract.get("provider_affinity", ()) or ()),
        install_requirements=tuple(contract.get("install_requirements", ()) or ()),
        attach_requirements=tuple(contract.get("attach_requirements", ()) or ()),
        commands=tuple(contract.get("commands", ()) or ()),
        verification=tuple(contract.get("verification", ()) or ()),
        source="discovery", source_ref=review.candidate_id)
    return promote_candidate(cand)


def _slug_id(text: str, *, limit: int = 32) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("-" if c in bad or c.isspace() else c for c in (text or "").strip().lower())
    out = "-".join(p for p in out.split("-") if p)
    return (out[:limit] or "candidate").rstrip("-")


def adoption_review_to_note(review: AdoptionReview, *, author: str = "user-researcher",
                            created_at: str = "", related: Sequence[str] = ()) -> str:
    """Author the review as a Hephaistos-readable structured vault evidence note."""

    from nexus.vault.note import build_authored_note

    body = "\n".join([
        "## 핵심 요약",
        f"- 후보: {review.title} (분류 {review.classification}, score {review.score})",
        f"- disposition: **{review.disposition}** — {review.disposition_rationale}",
        "",
        "## 도입 효율 검토 (8축)",
        f"1. current pain — {review.current_pain}",
        f"2. expected benefit — {review.expected_benefit}",
        f"3. overlap — {review.overlap}",
        f"4. operational cost — {review.operational_cost}",
        f"5. maintenance risk — {review.maintenance_risk}",
        f"6. provider/runtime fit — {review.provider_runtime_fit}",
        f"7. governance/security — {review.governance_security_impact}",
        f"8. adopt-now vs collect-first vs hold — {review.disposition}",
        "",
        "## 3축 검토 (PM / tech-lead / specialist)",
        f"- 요청 대상: {', '.join(review.consult.get('to_roles', ())) or '(없음)'}",
        f"- 질문: {review.consult.get('question', '')}",
        f"- 상태: {'검토 완료' if review.reviewed else '검토 대기 (collect-first — 즉시 활성화 안 함)'}",
        "",
        "## 적용 맥락",
        "- collect-first 면 evidence 만 누적, adopt-now 결정 시에만 armory intake(promote_candidate)로 연결.",
        "- adopted(검증된 catalog spec) ≠ equipped(register_promoted) — 장착은 별도 명시 단계.",
        "",
        "## 참고",
        f"- candidate_id: {review.candidate_id} · 출처: {review.source_id or 'operator'}",
    ])
    return build_authored_note(
        author, title=f"도입 검토: {review.title}", body=body, kind="adoption-review",
        status="draft", created_at=created_at, phase="discovery",
        source_flow="discovery-adoption", handoff_from="discovery", handoff_to="pm",
        tags=("forgekit", "discovery", "adoption-review", review.classification,
              review.disposition),
        related=tuple(related))


def persist_adoption_review(review: AdoptionReview, vault_root, *,
                            author: str = "user-researcher", created_at: str = "",
                            subdir: str = "00-inbox/discovery/adoption"):
    """Write the review as an authored vault evidence note. None if no vault / write fails."""

    if not vault_root:
        return None
    from nexus.vault.note import write_note

    content = adoption_review_to_note(review, author=author, created_at=created_at)
    return write_note(content, vault_root, f"{subdir}/adoption-{review.candidate_id}.md")


__all__ = (
    "CLASS_SIGNAL_ONLY", "CLASS_TOOL", "CLASS_IDEA", "CLASS_COMPETITOR",
    "CLASS_IMPL_REF", "CLASS_RISK", "ADOPT_NOW", "COLLECT_FIRST", "HOLD",
    "classify_candidate", "AdoptionReview", "build_adoption_review", "resolve_review",
    "adoption_to_armory_candidate", "adoption_review_to_note", "persist_adoption_review",
)
