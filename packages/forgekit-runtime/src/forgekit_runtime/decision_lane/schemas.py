"""PM / Tech-Lead lane — typed design-decision artifacts (pure dataclasses).

The *autopilot* chain (:mod:`forgekit_runtime.autopilot`) frames small repo
*findings* (docs/lint/test) and runs them PM→gateway→tech-lead so a SAFE class can
execute without the user. This lane is the **design-decision** sibling: a real
product brief, a recorded meeting, a stack comparison/recommendation, a tech-lead
signoff that fixes design-system + coding-convention + stack + tradeoff + approval,
and a single-executor engineer handoff.

Every artifact here is a frozen, serialisable dataclass so the lane stays pure and
testable. The *validators* (:mod:`.validators`) decide whether an artifact is real —
a meeting with no dissent or a signoff with no rationale is **rejected**, not
stamped. There is no path from a fake meeting/signoff to an engineer handoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# --- PM artifact -------------------------------------------------------------


@dataclass(frozen=True)
class PMBrief:
    """Product framing: the user problem, value, and acceptance bar (not a tech error)."""

    topic: str
    problem: str                                 # 사용자/운영자가 겪는 문제
    user_value: str                              # 풀면 누가 무엇을 체감하나
    target_users: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()    # 완료를 판단하는 검증 기준 (≥1)
    success_metrics: Tuple[str, ...] = ()        # 성공을 재는 지표 (≥1)
    constraints: Tuple[str, ...] = ()
    out_of_scope: Tuple[str, ...] = ()
    priority: str = "normal"                     # low / normal / high / urgent
    requested_by: str = "operator"

    def to_dict(self) -> dict:
        return {
            "topic": self.topic, "problem": self.problem, "user_value": self.user_value,
            "target_users": list(self.target_users),
            "acceptance_criteria": list(self.acceptance_criteria),
            "success_metrics": list(self.success_metrics),
            "constraints": list(self.constraints), "out_of_scope": list(self.out_of_scope),
            "priority": self.priority, "requested_by": self.requested_by,
        }


# --- stack comparison / recommendation ---------------------------------------


@dataclass(frozen=True)
class StackOption:
    """One candidate stack/approach with its honest pros AND cons."""

    name: str
    summary: str = ""
    pros: Tuple[str, ...] = ()
    cons: Tuple[str, ...] = ()
    risk: str = ""                               # 도입 리스크 한 줄
    fit: int = 0                                 # 0..100 적합도 (주관 점수, 비교용)

    def to_dict(self) -> dict:
        return {"name": self.name, "summary": self.summary, "pros": list(self.pros),
                "cons": list(self.cons), "risk": self.risk, "fit": self.fit}


@dataclass(frozen=True)
class StackComparison:
    """A real comparison: ≥2 options, a recommended one, rationale + tradeoffs."""

    decision_topic: str
    options: Tuple[StackOption, ...] = ()
    recommended: str = ""                        # 반드시 options 중 하나의 name
    rationale: str = ""                          # 왜 이 스택을 권고하나
    tradeoffs: Tuple[str, ...] = ()              # 권고안이 포기하는 것 (≥1)
    assumptions: Tuple[str, ...] = ()

    def option_names(self) -> Tuple[str, ...]:
        return tuple(o.name for o in self.options)

    def recommended_option(self) -> "StackOption | None":
        for o in self.options:
            if o.name == self.recommended:
                return o
        return None

    def to_dict(self) -> dict:
        return {"decision_topic": self.decision_topic,
                "options": [o.to_dict() for o in self.options],
                "recommended": self.recommended, "rationale": self.rationale,
                "tradeoffs": list(self.tradeoffs), "assumptions": list(self.assumptions)}


# --- consult note ------------------------------------------------------------


@dataclass(frozen=True)
class ConsultNote:
    """A recorded consult — one role asking another for input BEFORE a decision is fixed.

    The "consult like a company" artifact: **non-gating** (it does not advance the lane)
    but **real** — a topic, a requester role, ≥1 named consultee role, and a substantive
    question. So "we consulted X" leaves a durable, attributable trace instead of a claim;
    a consult with no consultee or no question is rejected by :func:`validate_consult`."""

    consult_id: str
    topic: str
    by_role: str                                 # 묻는 역할 (requester)
    to_roles: Tuple[str, ...] = ()               # consult 대상 역할 (≥1)
    question: str = ""                            # 무엇을 묻는지 (비어 있으면 fake)
    note: str = ""                               # 응답/논의 요약 (선택)
    refs: Tuple[str, ...] = ()                   # 참조 artifact id (brief/meeting/decision)

    def to_dict(self) -> dict:
        return {"consult_id": self.consult_id, "topic": self.topic, "by_role": self.by_role,
                "to_roles": list(self.to_roles), "question": self.question,
                "note": self.note, "refs": list(self.refs)}


# --- meeting artifact --------------------------------------------------------


@dataclass(frozen=True)
class ParticipantPosition:
    """One participant's stance in a meeting — the unit that makes consensus *real*."""

    role: str                                    # identity-registry id/alias
    stance: str                                  # support / oppose / conditional / neutral
    position: str                                # 발언 요지 (비어 있으면 fake)
    concerns: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"role": self.role, "stance": self.stance, "position": self.position,
                "concerns": list(self.concerns)}


# stances that count as genuine deliberation (dissent / conditional approval)
DISSENT_STANCES: Tuple[str, ...] = ("oppose", "conditional")
ALL_STANCES: Tuple[str, ...] = ("support", "oppose", "conditional", "neutral")


@dataclass(frozen=True)
class MeetingRecord:
    """A recorded design meeting. Real = ≥2 distinct roles, an agenda, and genuine
    deliberation (dissent or a raised concern) — not a rubber-stamp 'all support'."""

    meeting_id: str
    topic: str
    agenda: Tuple[str, ...] = ()
    participants: Tuple[ParticipantPosition, ...] = ()
    decisions: Tuple[str, ...] = ()
    open_questions: Tuple[str, ...] = ()
    round_index: int = 1
    escalated: bool = False                      # 합의 실패 → tech-lead escalation

    def roles(self) -> Tuple[str, ...]:
        return tuple(p.role for p in self.participants)

    def has_dissent(self) -> bool:
        return any(p.stance in DISSENT_STANCES or p.concerns for p in self.participants)

    def to_dict(self) -> dict:
        return {"meeting_id": self.meeting_id, "topic": self.topic,
                "agenda": list(self.agenda),
                "participants": [p.to_dict() for p in self.participants],
                "decisions": list(self.decisions), "open_questions": list(self.open_questions),
                "round_index": self.round_index, "escalated": self.escalated}


# --- tech-lead decision ------------------------------------------------------

# decision status lifecycle
DRAFT = "draft"
SIGNED_OFF = "signed_off"        # approve
CONDITIONAL = "conditional"      # approve with conditions
BLOCKED = "blocked"              # reject (restricted / not allowed)
ESCALATED = "escalated"          # disagreement → re-deliberate
NEEDS_INFO = "needs_info"        # request-more-info — bounce back for missing design inputs
DECISION_STATUSES: Tuple[str, ...] = (
    DRAFT, SIGNED_OFF, CONDITIONAL, BLOCKED, ESCALATED, NEEDS_INFO)


@dataclass(frozen=True)
class TechLeadDecision:
    """Tech-lead technical signoff. Ties a PM brief + a REAL meeting to a fixed
    design-system, coding-convention, stack decision, tradeoffs, and an approval
    level. ``signoff_by`` must resolve to the canonical ``tech-lead`` identity."""

    decision_id: str
    pm_brief_ref: str                            # PMBrief.topic 또는 외부 id
    meeting_ref: str                             # REQUIRED — MeetingRecord.meeting_id
    design_system: str = ""                      # 디자인 시스템 결정/참조
    coding_convention: str = ""                  # 코딩 컨벤션 결정/참조
    stack_decision: "StackComparison | None" = None
    tradeoffs: Tuple[str, ...] = ()
    integration_notes: Tuple[str, ...] = ()      # design system / API / infra 고려사항
    risk_class: str = "safe"                     # safe / risky / blocked
    approval_level: str = ""                     # autopilot.approval L*
    conditions: Tuple[str, ...] = ()
    rationale: str = ""
    signoff_by: str = "tech-lead"
    status: str = DRAFT

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id, "pm_brief_ref": self.pm_brief_ref,
            "meeting_ref": self.meeting_ref, "design_system": self.design_system,
            "coding_convention": self.coding_convention,
            "stack_decision": self.stack_decision.to_dict() if self.stack_decision else None,
            "tradeoffs": list(self.tradeoffs), "integration_notes": list(self.integration_notes),
            "risk_class": self.risk_class,
            "approval_level": self.approval_level, "conditions": list(self.conditions),
            "rationale": self.rationale, "signoff_by": self.signoff_by, "status": self.status,
        }


# --- engineer handoff --------------------------------------------------------


@dataclass(frozen=True)
class EngineerHandoff:
    """The single-executor work order handed to ONE engineer after a signed-off
    decision. Carries scope/forbidden-scope/test-strategy/rollback + acceptance."""

    handoff_id: str
    decision_ref: str                            # TechLeadDecision.decision_id
    executor_role: str                           # 단일 executor (registry engineer)
    scope: Tuple[str, ...] = ()
    forbidden_scope: Tuple[str, ...] = ()
    test_strategy: str = ""
    rollback_plan: str = ""
    acceptance_criteria: Tuple[str, ...] = ()    # PM brief 에서 carry
    operator_required: bool = False              # risky/blocked → 운영자 승인 필요

    def to_dict(self) -> dict:
        return {"handoff_id": self.handoff_id, "decision_ref": self.decision_ref,
                "executor_role": self.executor_role, "scope": list(self.scope),
                "forbidden_scope": list(self.forbidden_scope),
                "test_strategy": self.test_strategy, "rollback_plan": self.rollback_plan,
                "acceptance_criteria": list(self.acceptance_criteria),
                "operator_required": self.operator_required}


# --- specialist briefing (the materialized work order) -----------------------


@dataclass(frozen=True)
class RejectedOption:
    """A stack option that was considered and NOT chosen — carried so the specialist
    sees what was weighed and why it lost (no silent 'just use X')."""

    name: str
    why_not: str = ""                            # 왜 탈락했나 (cons/risk 요약)

    def to_dict(self) -> dict:
        return {"name": self.name, "why_not": self.why_not}


@dataclass(frozen=True)
class SpecialistBriefing:
    """The full work briefing a specialist receives — composed from PM brief + tech-lead
    decision + engineer handoff. A real-company work order, not a bare 'go build it':
    goal, proposed stack + WHY, the REJECTED options, coding conventions, design-system,
    API/infra considerations, scope, test strategy, and acceptance. Built by
    :func:`build_specialist_briefing`; a briefing missing the design context is rejected by
    :func:`validate_specialist_briefing`, so a specialist never starts off a thin order
    (the point: reduce 'design 없이 바로 구현')."""

    handoff_id: str
    executor_role: str
    decision_ref: str
    goal: str = ""                               # PM 목표 (problem → user_value)
    proposed_stack: str = ""                     # 채택된 스택/접근
    proposed_stack_summary: str = ""
    stack_rationale: str = ""                    # 왜 이 스택
    rejected_options: Tuple[RejectedOption, ...] = ()   # 탈락안 + 왜
    coding_conventions: str = ""
    design_system: str = ""
    integration_notes: Tuple[str, ...] = ()      # design system / API / infra 고려
    scope: Tuple[str, ...] = ()
    forbidden_scope: Tuple[str, ...] = ()
    test_strategy: str = ""
    rollback_plan: str = ""
    acceptance_criteria: Tuple[str, ...] = ()
    operator_required: bool = False

    def to_dict(self) -> dict:
        return {
            "handoff_id": self.handoff_id, "executor_role": self.executor_role,
            "decision_ref": self.decision_ref, "goal": self.goal,
            "proposed_stack": self.proposed_stack,
            "proposed_stack_summary": self.proposed_stack_summary,
            "stack_rationale": self.stack_rationale,
            "rejected_options": [r.to_dict() for r in self.rejected_options],
            "coding_conventions": self.coding_conventions, "design_system": self.design_system,
            "integration_notes": list(self.integration_notes), "scope": list(self.scope),
            "forbidden_scope": list(self.forbidden_scope), "test_strategy": self.test_strategy,
            "rollback_plan": self.rollback_plan,
            "acceptance_criteria": list(self.acceptance_criteria),
            "operator_required": self.operator_required,
        }

    def lines(self) -> Tuple[str, ...]:
        """Operator/specialist-readable work order."""
        out = [f"work order {self.handoff_id} → {self.executor_role}"
               + ("  · ⚠ operator 승인 필요" if self.operator_required else ""),
               f"  목표: {self.goal}",
               f"  제안 스택: {self.proposed_stack}"
               + (f" — {self.proposed_stack_summary}" if self.proposed_stack_summary else ""),
               f"  선택 이유: {self.stack_rationale}"]
        for r in self.rejected_options:
            out.append(f"  ✗ 탈락: {r.name} — {r.why_not}")
        out.append(f"  코딩 컨벤션: {self.coding_conventions}")
        out.append(f"  디자인 시스템: {self.design_system}")
        for n in self.integration_notes:
            out.append(f"  · API/infra: {n}")
        for s in self.scope:
            out.append(f"  ☐ scope: {s}")
        for s in self.forbidden_scope:
            out.append(f"  ⊘ 금지: {s}")
        out.append(f"  test 전략: {self.test_strategy}")
        if self.rollback_plan:
            out.append(f"  rollback: {self.rollback_plan}")
        for a in self.acceptance_criteria:
            out.append(f"  ✓ acceptance: {a}")
        return tuple(out)


__all__ = (
    "PMBrief", "StackOption", "StackComparison", "ConsultNote", "ParticipantPosition",
    "MeetingRecord", "TechLeadDecision", "EngineerHandoff",
    "RejectedOption", "SpecialistBriefing",
    "DISSENT_STANCES", "ALL_STANCES",
    "DRAFT", "SIGNED_OFF", "CONDITIONAL", "BLOCKED", "ESCALATED", "NEEDS_INFO", "DECISION_STATUSES",
)
