"""Internal approval chain (repo-autopilot WT1) — PM → gateway → tech-lead, enforced.

``run_internal_chain`` takes a RepoFinding and runs it through PM (structure +
user-value), gateway (route to an owner), and tech-lead (classify + signoff). A
specialist may execute ONLY when a TechLeadDecision exists AND it cleared the
internal bar (safe class, L2). Risky → needs the user; restricted → operator only.
So "user 승인 없음" is allowed (safe/L2), but "internal 승인 없음" is never — there is
no path to execution without a TechLeadDecision. Pure → testable.
"""

from __future__ import annotations

from typing import Optional, Tuple

from . import approval as A
from .artifacts import (
    GatewayRoute,
    PMPacket,
    RepoFinding,
    TechLeadDecision,
    VaultTraceNote,
)

# finding kind → engineering owner (gateway routing)
_OWNER_BY_KIND = {
    "docs": "tech-lead", "test": "qa", "lint": "be", "ops": "devops",
    "discomfort": "fe", "gap": "be",
}

# approval level → tech-lead decision class
_CLASS_BY_LEVEL = {
    A.L2_INTERNAL_APPROVE: "safe",
    A.L3_USER_APPROVE: "risky",
    A.L4_RESTRICTED: "blocked",
}


def pm_structure(finding: RepoFinding) -> PMPacket:
    """PM: frame the finding as user value (not just a tech error)."""

    return PMPacket(
        finding=finding,
        why_it_matters=f"'{finding.finding}' 는 유지보수/신뢰/사용자 경험에 영향",
        user_value="개선 시 operator/사용자가 체감하는 마찰 감소",
        missing=("acceptance criteria", "검증 방법"),
        recommended_owner=_OWNER_BY_KIND.get(finding.kind, "tech-lead"),
    )


def gateway_route(packet: PMPacket) -> GatewayRoute:
    """Gateway: forward to the appropriate owner role."""

    return GatewayRoute(
        packet_summary=packet.finding.finding,
        owner_role=packet.recommended_owner,
        route_reason=f"kind={packet.finding.kind} → {packet.recommended_owner}",
    )


def tech_lead_signoff(packet: PMPacket, route: GatewayRoute, *, risk_class: str = "") -> TechLeadDecision:
    """Tech-lead: classify + signoff. can_execute only for internal-approved safe class."""

    level = A.classify_level(packet.finding.finding, risk_class=risk_class)
    decision_class = _CLASS_BY_LEVEL.get(level, "risky")
    can_exec = A.autopilot_can_execute(level)
    rationale = {
        "safe": "safe class — 내부 승인(PM→gateway→tech-lead)으로 실행 가능, user 승인 불요",
        "risky": "risky — user 승인 필요(L3), autopilot 은 propose 까지만",
        "blocked": "restricted — deploy/secret/infra, 자동 실행 금지(L4), operator+runbook",
    }.get(decision_class, "")
    return TechLeadDecision(
        packet_summary=packet.finding.finding, decision_class=decision_class,
        approval_level=level, can_execute=can_exec, rationale=rationale)


def run_internal_chain(finding: RepoFinding, *, risk_class: str = ""
                       ) -> Tuple[PMPacket, GatewayRoute, TechLeadDecision, Tuple[str, ...]]:
    """Full internal chain: finding → PM → gateway → tech-lead (+ phase trace)."""

    packet = pm_structure(finding)
    route = gateway_route(packet)
    decision = tech_lead_signoff(packet, route, risk_class=risk_class)
    trace = ("pm:structure", f"gateway:route→{route.owner_role}",
             f"tech-lead:{decision.decision_class}/{decision.approval_level}")
    return packet, route, decision, trace


def can_specialist_execute(decision: Optional[TechLeadDecision]) -> bool:
    """A specialist may ONLY execute on a TechLeadDecision that cleared the internal bar.

    No decision → False (no internal approval). Decision present but not safe/L2 →
    False (needs user/operator). This is the hard "no internal signoff, no execution".
    """

    return decision is not None and bool(decision.can_execute)


def trace_note(who: str, decision: TechLeadDecision, *, what: str = "", trace=()) -> VaultTraceNote:
    """Build the vault trace note for an executed/proposed chain step."""

    return VaultTraceNote(
        who=who, why=decision.rationale, what=what or decision.packet_summary,
        approval_chain=tuple(trace) + (f"signoff:{decision.signoff_by}",),
        area=decision.decision_class)


__all__ = (
    "pm_structure", "gateway_route", "tech_lead_signoff", "run_internal_chain",
    "can_specialist_execute", "trace_note",
)
