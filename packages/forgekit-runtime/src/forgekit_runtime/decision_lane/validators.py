"""PM / Tech-Lead lane validators — the anti-fake gate.

Each validator returns a tuple of human-readable violation strings; ``()`` means the
artifact is real and may advance. The lane (:mod:`.lane`) refuses to advance any
artifact whose validator is non-empty, so there is **no path from a fake meeting or a
fake signoff to an engineer handoff**.

What "fake" means here, concretely:
* a meeting with <2 distinct roles, an empty agenda, empty positions, or zero
  deliberation (everyone "support", no concern raised) → fake consensus, rejected;
* a signoff missing its meeting reference, rationale, design-system, coding-
  convention, a real stack comparison, or a tradeoff → fake signoff, rejected;
* a signoff whose ``signoff_by`` does not resolve to the canonical ``tech-lead``
  identity → not the tech-lead, rejected.

Role identities are resolved through the ONE registry SSoT
(:mod:`forgekit_config.identity.registry`) — abbreviations like ``fe`` normalise to
``frontend-engineer`` there, never re-mapped here.
"""

from __future__ import annotations

from typing import Tuple

from forgekit_config.identity.registry import canonical_id, resolve_identity

from .schemas import (
    BLOCKED,
    CONDITIONAL,
    ESCALATED,
    SIGNED_OFF,
    ConsultNote,
    EngineerHandoff,
    MeetingRecord,
    PMBrief,
    StackComparison,
    TechLeadDecision,
)

# canonical roles that may NOT be the single engineer executor (they decide/route)
NON_EXECUTOR_ROLES = frozenset({"gateway", "tech-lead", "product-manager"})
_NON_EXECUTOR = NON_EXECUTOR_ROLES  # backward-compat alias


def _blank(s: str) -> bool:
    return not (s or "").strip()


# --- PM brief ----------------------------------------------------------------


def validate_pm_brief(brief: PMBrief) -> Tuple[str, ...]:
    """A real PM brief frames the problem + user value AND fixes the acceptance bar."""

    v = []
    if _blank(brief.topic):
        v.append("PM brief: topic 비어 있음")
    if _blank(brief.problem):
        v.append("PM brief: problem(사용자 문제) 비어 있음")
    if _blank(brief.user_value):
        v.append("PM brief: user_value 비어 있음")
    if not brief.acceptance_criteria:
        v.append("PM brief: acceptance_criteria 최소 1개 필요 (완료 판단 기준 없음)")
    if not brief.success_metrics:
        v.append("PM brief: success_metrics 최소 1개 필요 (성공 지표 없음)")
    return tuple(v)


# --- stack comparison --------------------------------------------------------


def validate_stack_comparison(cmp: StackComparison) -> Tuple[str, ...]:
    """A real comparison weighs ≥2 options (each with pros AND cons) and recommends one."""

    v = []
    if _blank(cmp.decision_topic):
        v.append("stack: decision_topic 비어 있음")
    if len(cmp.options) < 2:
        v.append("stack: 후보 옵션이 2개 미만 — 비교가 아님")
    for o in cmp.options:
        if _blank(o.name):
            v.append("stack: 이름 없는 옵션")
            continue
        if not o.pros:
            v.append(f"stack: '{o.name}' 의 장점(pros) 없음")
        if not o.cons:
            v.append(f"stack: '{o.name}' 의 단점(cons) 없음 — 한쪽만 보는 fake 비교")
    if _blank(cmp.recommended):
        v.append("stack: 권고안(recommended) 없음")
    elif cmp.recommended not in cmp.option_names():
        v.append(f"stack: 권고안 '{cmp.recommended}' 이 옵션 목록에 없음")
    if _blank(cmp.rationale):
        v.append("stack: 권고 근거(rationale) 없음")
    if not cmp.tradeoffs:
        v.append("stack: tradeoff 최소 1개 필요 — 공짜 선택은 없음")
    return tuple(v)


# --- consult note (anti-fake, non-gating) ------------------------------------


def validate_consult(note: ConsultNote) -> Tuple[str, ...]:
    """A real consult names a requester role, ≥1 consultee role, and a substantive
    question. Roles resolve through the registry SSoT. Non-gating (it never advances the
    lane) but it is NOT freeform prose — an empty question or no consultee is rejected, so
    'we consulted X' cannot be a bare claim."""

    v = []
    if _blank(note.consult_id):
        v.append("consult: consult_id 비어 있음")
    if _blank(note.topic):
        v.append("consult: topic 비어 있음")
    if _blank(note.by_role):
        v.append("consult: by_role(요청자) 비어 있음")
    elif not canonical_id(note.by_role):
        v.append(f"consult: by_role '{note.by_role}' 이 식별자 레지스트리에 없음")
    if not note.to_roles:
        v.append("consult: to_roles(consult 대상) 최소 1개 필요 — 혼잣말은 consult 아님")
    else:
        for r in note.to_roles:
            if not canonical_id(r):
                v.append(f"consult: to_role '{r}' 이 식별자 레지스트리에 없음")
    if _blank(note.question):
        v.append("consult: question(무엇을 묻는지) 비어 있음 — freeform prose 금지")
    return tuple(v)


# --- meeting (anti-fake consensus) -------------------------------------------


def validate_meeting(meeting: MeetingRecord) -> Tuple[str, ...]:
    """Reject a fake meeting: <2 distinct roles, no agenda, empty positions, or zero
    deliberation (no dissent and no concern). A real meeting either decides or escalates."""

    v = []
    if _blank(meeting.meeting_id):
        v.append("meeting: meeting_id 비어 있음")
    if _blank(meeting.topic):
        v.append("meeting: topic 비어 있음")
    if not meeting.agenda:
        v.append("meeting: agenda 최소 1개 필요")

    canon = [canonical_id(p.role) or p.role.strip() for p in meeting.participants]
    if len(meeting.participants) < 2:
        v.append("meeting: 참석자 2명 미만 — 토의가 성립하지 않음")
    if len(set(c for c in canon if c)) < 2:
        v.append("meeting: 서로 다른 역할 2개 미만 — fake 단독 합의")
    for p in meeting.participants:
        if _blank(p.position):
            v.append(f"meeting: '{p.role}' 의 발언(position) 비어 있음 — fake 참석")
        if p.stance and p.stance not in ("support", "oppose", "conditional", "neutral"):
            v.append(f"meeting: '{p.role}' 의 stance '{p.stance}' 알 수 없음")

    if not meeting.has_dissent():
        v.append("meeting: 반대/조건부/우려가 하나도 없음 — rubber-stamp(fake) 합의")
    if not meeting.decisions and not meeting.escalated:
        v.append("meeting: 결정도 escalation 도 없음 — 미결 회의")
    return tuple(v)


# --- tech-lead decision (anti-fake signoff) ----------------------------------


def validate_tech_lead_decision(decision: TechLeadDecision) -> Tuple[str, ...]:
    """Reject a fake signoff. A signed/conditional decision MUST reference a real
    meeting and fix design-system + coding-convention + stack + tradeoff + rationale,
    and ``signoff_by`` MUST be the canonical tech-lead."""

    v = []
    if _blank(decision.decision_id):
        v.append("signoff: decision_id 비어 있음")
    if _blank(decision.pm_brief_ref):
        v.append("signoff: pm_brief_ref 없음 — PM 입력과 연결되지 않음")
    if _blank(decision.meeting_ref):
        v.append("signoff: meeting_ref 없음 — 회의 없는 fake 승인")

    # the tech-lead, and only the tech-lead, may sign off
    if canonical_id(decision.signoff_by) != "tech-lead":
        v.append(f"signoff: '{decision.signoff_by}' 은 tech-lead 가 아님 — 권한 없는 승인")

    # design-doc must-haves (the 5 mandated fields)
    if _blank(decision.design_system):
        v.append("signoff: design_system 비어 있음")
    if _blank(decision.coding_convention):
        v.append("signoff: coding_convention 비어 있음")
    if decision.stack_decision is None:
        v.append("signoff: stack_decision 없음")
    else:
        v.extend(validate_stack_comparison(decision.stack_decision))
    if not decision.tradeoffs:
        v.append("signoff: tradeoff 최소 1개 필요")
    if _blank(decision.rationale):
        v.append("signoff: rationale 비어 있음")

    if decision.risk_class not in ("safe", "risky", "blocked"):
        v.append(f"signoff: risk_class '{decision.risk_class}' 알 수 없음")
    if decision.status == CONDITIONAL and not decision.conditions:
        v.append("signoff: conditional 인데 conditions 가 비어 있음")
    return tuple(v)


# --- engineer handoff --------------------------------------------------------


def validate_handoff(handoff: EngineerHandoff, decision: TechLeadDecision) -> Tuple[str, ...]:
    """A handoff is valid only off a signed/conditional decision, to a SINGLE engineer
    (registry engineering role, never gateway/tech-lead/PM), with scope + test strategy."""

    v = []
    if _blank(handoff.handoff_id):
        v.append("handoff: handoff_id 비어 있음")
    if handoff.decision_ref != decision.decision_id:
        v.append("handoff: decision_ref 가 결정과 불일치")
    if decision.status not in (SIGNED_OFF, CONDITIONAL):
        v.append(f"handoff: 결정 status '{decision.status}' — 서명되지 않은 결정에서 인계 금지")

    cid = canonical_id(handoff.executor_role)
    if not cid:
        v.append(f"handoff: executor_role '{handoff.executor_role}' 이 식별자 레지스트리에 없음")
    else:
        ident = resolve_identity(cid)
        if cid in _NON_EXECUTOR or ident.department != "engineering":
            v.append(f"handoff: '{cid}' 은 단일 executor 엔지니어가 아님 (router/decider 역할)")

    if not handoff.scope:
        v.append("handoff: scope 비어 있음")
    if _blank(handoff.test_strategy):
        v.append("handoff: test_strategy 없음 — 검증 전략 없는 인계 금지")
    return tuple(v)


__all__ = (
    "validate_pm_brief", "validate_stack_comparison", "validate_consult",
    "validate_meeting", "validate_tech_lead_decision", "validate_handoff",
    "NON_EXECUTOR_ROLES",
)
