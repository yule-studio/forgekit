"""PM / Tech-Lead lane orchestration — gateway → tech-lead → engineer, enforced.

The handoff rule, in one line: **gateway routes (decides nothing), tech-lead signs off
the design (the only technical authority), an engineer executes (single executor) — and
an engineer may start ONLY on a signed/conditional :class:`TechLeadDecision` that itself
references a real meeting.** This mirrors the autopilot rule "no internal signoff, no
execution" (:func:`forgekit_runtime.autopilot.chain.can_specialist_execute`) but for
design decisions.

Approval levels are reused from :mod:`forgekit_runtime.autopilot.approval` so the L0–L4
ladder lives in ONE place: a SAFE (L2) design can be handed off without the user; a RISKY
(L3) one is signed off but ``operator_required``; a RESTRICTED (L4) one is BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from ..autopilot import approval as A
from .schemas import (
    BLOCKED,
    CONDITIONAL,
    DRAFT,
    ESCALATED,
    NEEDS_INFO,
    SIGNED_OFF,
    EngineerHandoff,
    MeetingRecord,
    PMBrief,
    StackComparison,
    TechLeadDecision,
)
from .validators import (
    validate_handoff,
    validate_meeting,
    validate_pm_brief,
    validate_tech_lead_decision,
)

# autopilot decision_class ↔ approval level (shared ladder)
_LEVEL_TO_CLASS = {
    A.L2_INTERNAL_APPROVE: "safe",
    A.L3_USER_APPROVE: "risky",
    A.L4_RESTRICTED: "blocked",
}


@dataclass(frozen=True)
class GatewayRouting:
    """Gateway output: forward to tech-lead, or refuse — it decides no technical content."""

    topic: str
    forwarded: bool
    to_role: str = "tech-lead"
    blocking_violations: Tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        return {"topic": self.topic, "forwarded": self.forwarded, "to_role": self.to_role,
                "blocking_violations": list(self.blocking_violations), "reason": self.reason}


@dataclass(frozen=True)
class LaneResult:
    """End-to-end lane trace: routing → decision → (handoff). ``engineer_may_start`` is
    the single hard gate the executor checks."""

    routing: GatewayRouting
    decision: Optional[TechLeadDecision] = None
    handoff: Optional[EngineerHandoff] = None
    engineer_may_start: bool = False
    operator_required: bool = False
    violations: Tuple[str, ...] = ()
    trace: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "routing": self.routing.to_dict(),
            "decision": self.decision.to_dict() if self.decision else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "engineer_may_start": self.engineer_may_start,
            "operator_required": self.operator_required,
            "violations": list(self.violations), "trace": list(self.trace),
        }


# --- stage 1: gateway --------------------------------------------------------


def route_to_tech_lead(brief: PMBrief, meeting: MeetingRecord) -> GatewayRouting:
    """Gateway: validate that the PM brief AND meeting are real, then forward to tech-lead.

    The gateway makes NO technical decision — it only refuses to forward fake inputs
    (so a fake meeting never reaches a signoff surface)."""

    blocking = validate_pm_brief(brief) + validate_meeting(meeting)
    if blocking:
        return GatewayRouting(topic=brief.topic, forwarded=False,
                              blocking_violations=blocking,
                              reason="PM brief/meeting 이 불완전 — tech-lead 로 전달 차단")
    return GatewayRouting(topic=brief.topic, forwarded=True,
                          reason="brief+meeting 실재 확인 — tech-lead 기술 승인으로 라우팅")


# --- stage 2: tech-lead ------------------------------------------------------


def tech_lead_decide(
    brief: PMBrief,
    meeting: MeetingRecord,
    stack: StackComparison,
    *,
    design_system: str,
    coding_convention: str,
    risk_class: str = "",
    conditions: Tuple[str, ...] = (),
    rationale: str = "",
    signoff_by: str = "tech-lead",
    decision_id: str = "",
) -> TechLeadDecision:
    """Tech-lead: classify the design's risk, then sign off / conditional / block /
    escalate. A signoff is produced ONLY when the validators pass — a fake (empty
    rationale, missing meeting, non-tech-lead signer, one-sided stack) is downgraded to
    ``escalated``, never stamped."""

    level = A.classify_level(f"{stack.decision_topic} {rationale} {risk_class}",
                             risk_class=risk_class)
    klass = _LEVEL_TO_CLASS.get(level, "risky")
    did = decision_id or f"decision:{meeting.meeting_id}"

    # intended status from the risk ladder
    if meeting.escalated:
        status = ESCALATED
    elif klass == "blocked":
        status = BLOCKED
    elif conditions:
        status = CONDITIONAL
    else:
        status = SIGNED_OFF

    candidate = TechLeadDecision(
        decision_id=did, pm_brief_ref=brief.topic, meeting_ref=meeting.meeting_id,
        design_system=design_system, coding_convention=coding_convention,
        stack_decision=stack, tradeoffs=stack.tradeoffs, risk_class=klass,
        approval_level=level, conditions=tuple(conditions),
        rationale=rationale or stack.rationale, signoff_by=signoff_by, status=status,
    )

    # no fake signoff: if the artifact isn't real, it cannot be signed/conditional
    if status in (SIGNED_OFF, CONDITIONAL) and validate_tech_lead_decision(candidate):
        from dataclasses import replace
        return replace(candidate, status=ESCALATED)
    return candidate


# --- stage 3: engineer handoff ----------------------------------------------


def handoff_to_engineer(
    decision: TechLeadDecision,
    executor_role: str,
    *,
    scope: Tuple[str, ...],
    test_strategy: str,
    forbidden_scope: Tuple[str, ...] = (),
    rollback_plan: str = "",
    acceptance_criteria: Tuple[str, ...] = (),
    handoff_id: str = "",
) -> EngineerHandoff:
    """Build the single-executor work order. ``operator_required`` is True unless the
    design cleared the internal SAFE bar (L2) — risky/blocked still need the operator."""

    operator_required = not A.autopilot_can_execute(decision.approval_level)
    return EngineerHandoff(
        handoff_id=handoff_id or f"handoff:{decision.decision_id}",
        decision_ref=decision.decision_id, executor_role=executor_role,
        scope=tuple(scope), forbidden_scope=tuple(forbidden_scope),
        test_strategy=test_strategy, rollback_plan=rollback_plan,
        acceptance_criteria=tuple(acceptance_criteria), operator_required=operator_required,
    )


def can_engineer_start(decision: Optional[TechLeadDecision],
                       handoff: Optional[EngineerHandoff]) -> bool:
    """The hard gate: an engineer starts ONLY on a real, signed/conditional decision with
    a valid handoff. No decision / unsigned / failing validators → False."""

    if decision is None or handoff is None:
        return False
    if decision.status not in (SIGNED_OFF, CONDITIONAL):
        return False
    if validate_tech_lead_decision(decision):
        return False
    if validate_handoff(handoff, decision):
        return False
    return True


def tech_lead_request_more_info(
    brief: PMBrief,
    meeting: MeetingRecord,
    *,
    info_requested: Tuple[str, ...],
    decision_id: str = "",
    signoff_by: str = "tech-lead",
) -> TechLeadDecision:
    """Tech-lead's *request-more-info* verdict — the design inputs (stack / design system /
    coding convention / tradeoff) are insufficient to sign, so the lane bounces back rather
    than being approved or rejected. ``status=NEEDS_INFO``; the requested items ride in
    ``conditions``. A ``needs_info`` decision is never executable (``can_engineer_start``
    requires signed/conditional), so a specialist still cannot start off it."""

    return TechLeadDecision(
        decision_id=decision_id or f"decision:{meeting.meeting_id}",
        pm_brief_ref=brief.topic, meeting_ref=meeting.meeting_id,
        conditions=tuple(info_requested), signoff_by=signoff_by, status=NEEDS_INFO,
        rationale="설계 입력(스택/디자인시스템/코딩컨벤션/tradeoff) 부족 — 추가정보 요청(승인/반려 아님)")


# --- full pipeline -----------------------------------------------------------


def run_lane(
    brief: PMBrief,
    meeting: MeetingRecord,
    stack: StackComparison,
    *,
    design_system: str,
    coding_convention: str,
    executor_role: str,
    scope: Tuple[str, ...],
    test_strategy: str,
    risk_class: str = "",
    conditions: Tuple[str, ...] = (),
    rationale: str = "",
    forbidden_scope: Tuple[str, ...] = (),
    rollback_plan: str = "",
    signoff_by: str = "tech-lead",
) -> LaneResult:
    """Run PM brief + meeting → gateway → tech-lead → engineer, end to end, with a trace.

    Returns a :class:`LaneResult`; ``engineer_may_start`` is only True when every stage
    is real and the decision cleared signoff. No fake stage can produce a startable handoff."""

    trace = []
    routing = route_to_tech_lead(brief, meeting)
    trace.append("gateway:route" + ("→tech-lead" if routing.forwarded else "→blocked"))
    if not routing.forwarded:
        return LaneResult(routing=routing, violations=routing.blocking_violations,
                          trace=tuple(trace))

    decision = tech_lead_decide(
        brief, meeting, stack, design_system=design_system,
        coding_convention=coding_convention, risk_class=risk_class,
        conditions=conditions, rationale=rationale, signoff_by=signoff_by)
    trace.append(f"tech-lead:{decision.status}/{decision.approval_level}")

    if decision.status not in (SIGNED_OFF, CONDITIONAL):
        return LaneResult(routing=routing, decision=decision,
                          violations=validate_tech_lead_decision(decision),
                          trace=tuple(trace))

    handoff = handoff_to_engineer(
        decision, executor_role, scope=scope, test_strategy=test_strategy,
        forbidden_scope=forbidden_scope, rollback_plan=rollback_plan,
        acceptance_criteria=brief.acceptance_criteria)
    trace.append(f"engineer:handoff→{executor_role}")

    h_viol = validate_handoff(handoff, decision)
    may_start = can_engineer_start(decision, handoff)
    trace.append("engineer:start" if may_start else "engineer:blocked")
    return LaneResult(routing=routing, decision=decision, handoff=handoff,
                      engineer_may_start=may_start,
                      operator_required=handoff.operator_required,
                      violations=h_viol, trace=tuple(trace))


__all__ = (
    "GatewayRouting", "LaneResult", "route_to_tech_lead", "tech_lead_decide",
    "handoff_to_engineer", "can_engineer_start", "run_lane", "tech_lead_request_more_info",
)
