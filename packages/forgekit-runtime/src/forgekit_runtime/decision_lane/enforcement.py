"""PM / Tech-Lead lane → real runtime execution binding (governance teeth).

The lane (:mod:`.lane`) decides; this module is what makes the decision *bite* at the
moment of execution. Every would-be execution must pass :func:`authorize_execution`,
which re-checks the WHOLE approval chain against the ACTUAL action — not the action the
decision was signed for:

* **gateway** routed it (``GatewayRouting.forwarded``),
* **tech-lead** signed it (a real, validated :class:`TechLeadDecision`),
* the action, **classified at execution time**, is safe / risky / destructive, and
* **operator** approval exists when the action is risky; destructive never auto-runs.

Class is recomputed here so signing a "safe" change can't smuggle a ``deploy`` past the
gate (scope creep → blocked, re-signoff required). The authorized verdict carries the
approval metadata that the commit MUST then carry (:func:`execution_commit_trailers` /
:func:`validate_execution_trailers`) — so the work path is bound to the approval, and a
commit with fake/absent approval metadata is rejected.

There is no path to execution without this verdict — :func:`assert_executable` raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from forgekit_config.identity.attribution import commit_trailers
from forgekit_config.identity.registry import canonical_id

from ..autopilot import approval as A
from ..autopilot.artifacts import TechLeadDecision as AutopilotDecision
from ..autopilot.execution import AUTO_FORBIDDEN, SAFE_CLASS_ALLOWLIST
from .lane import GatewayRouting
from .schemas import CONDITIONAL, SIGNED_OFF, EngineerHandoff, TechLeadDecision
from .validators import validate_handoff, validate_tech_lead_decision

# action classes on the execution path
SAFE = "safe"
RISKY = "risky"
DESTRUCTIVE = "destructive"

_LEVEL_ORDER = {A.L2_INTERNAL_APPROVE: 0, A.L3_USER_APPROVE: 1, A.L4_RESTRICTED: 2}
_CLASS_BY_LEVEL = {A.L2_INTERNAL_APPROVE: SAFE, A.L3_USER_APPROVE: RISKY,
                   A.L4_RESTRICTED: DESTRUCTIVE}


class ExecutionBlocked(RuntimeError):
    """Raised by :func:`assert_executable` when the approval chain does not authorize."""

    def __init__(self, reasons: Tuple[str, ...]):
        self.reasons = tuple(reasons)
        super().__init__("; ".join(reasons) or "execution blocked")


@dataclass(frozen=True)
class ActionRequest:
    """The ACTUAL action an engineer is about to perform (classified independently)."""

    kind: str = ""                 # docs/tests/lint/deploy/secret/... (allowlist/forbidden)
    summary: str = ""              # free text — classified for risky/restricted wording
    diff: int = 0
    files: int = 0
    risk_flag: str = ""            # "", "risky", "blocked" (caller hint)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "summary": self.summary, "diff": self.diff,
                "files": self.files, "risk_flag": self.risk_flag}


@dataclass(frozen=True)
class OperatorApproval:
    """A real operator grant for a risky action. Empty approver / mismatched ref = fake."""

    approver: str                  # operator/gateway identity granting the approval
    decision_ref: str              # MUST equal TechLeadDecision.decision_id
    approved: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {"approver": self.approver, "decision_ref": self.decision_ref,
                "approved": self.approved, "note": self.note}


@dataclass(frozen=True)
class ExecutionVerdict:
    """The single source of truth for "may this execute, and under what approval"."""

    allowed: bool
    action_class: str              # safe / risky / destructive
    approval_level: str
    executor_id: str = ""
    approval_metadata: str = ""    # what the commit MUST carry (Forgekit-Approval)
    satisfied: Tuple[str, ...] = ()        # approvals present (gateway/tech-lead/operator…)
    blocking_reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "action_class": self.action_class,
                "approval_level": self.approval_level, "executor_id": self.executor_id,
                "approval_metadata": self.approval_metadata, "satisfied": list(self.satisfied),
                "blocking_reasons": list(self.blocking_reasons)}


def classify_action(request: ActionRequest) -> Tuple[str, str]:
    """Classify the ACTUAL action → (action_class, approval_level). Safe-by-rejection:
    a forbidden kind is destructive; an UNKNOWN kind is never auto-safe (→ risky)."""

    level = A.classify_level(f"{request.kind} {request.summary} {request.risk_flag}",
                             risk_class=request.risk_flag)
    kind = (request.kind or "").strip()
    if kind in AUTO_FORBIDDEN:
        level = A.L4_RESTRICTED
    elif kind and kind not in SAFE_CLASS_ALLOWLIST and level == A.L2_INTERNAL_APPROVE:
        level = A.L3_USER_APPROVE      # unknown kind isn't auto-safe
    return _CLASS_BY_LEVEL.get(level, RISKY), level


def authorize_execution(
    decision: Optional[TechLeadDecision],
    handoff: Optional[EngineerHandoff],
    request: ActionRequest,
    *,
    routing: Optional[GatewayRouting] = None,
    operator_approval: Optional[OperatorApproval] = None,
) -> ExecutionVerdict:
    """Re-check the FULL chain against the ACTUAL action. Returns a verdict; never raises."""

    reasons = []
    satisfied = []

    # gateway approval — routed, or it never legitimately reached execution
    if routing is None or not routing.forwarded:
        reasons.append("gateway 승인(라우팅) 없음 — 미경유 실행")
    else:
        satisfied.append("gateway")

    # tech-lead technical signoff — real and validated
    if decision is None:
        reasons.append("tech-lead 결정 없음")
    elif decision.status not in (SIGNED_OFF, CONDITIONAL):
        reasons.append(f"tech-lead 서명 안 됨(status={decision.status})")
    elif validate_tech_lead_decision(decision):
        reasons.append("tech-lead 서명 무효(fake signoff)")
    else:
        satisfied.append("tech-lead")

    # engineer handoff — single executor, valid
    if handoff is None:
        reasons.append("engineer handoff 없음")
    elif decision is not None and validate_handoff(handoff, decision):
        reasons.append("handoff 무효")
    else:
        satisfied.append("handoff")

    # classify the ACTUAL action at execution time
    action_class, level = classify_action(request)

    # scope-creep guard: actual action must not exceed the signed level
    if decision is not None and decision.approval_level:
        if _LEVEL_ORDER.get(level, 1) > _LEVEL_ORDER.get(decision.approval_level, 1):
            reasons.append(
                f"execution class({action_class}/{level}) > 서명 범위"
                f"({decision.approval_level}) — 재서명 필요")

    # class-specific approval requirement
    if action_class == DESTRUCTIVE:
        reasons.append("destructive(L4) — 자동 실행 금지, operator + runbook 전용")
    elif action_class == RISKY:
        if (operator_approval is None or not operator_approval.approved
                or decision is None
                or operator_approval.decision_ref != decision.decision_id):
            reasons.append("risky(L3) — operator 승인 없음/대상 불일치")
        else:
            satisfied.append("operator")
    else:  # safe
        satisfied.append("internal-safe")

    executor_id = canonical_id(handoff.executor_role) if handoff else ""
    signoff_id = canonical_id(decision.signoff_by) if decision else ""
    meta = ""
    if decision is not None:
        meta = f"decision={decision.decision_id};level={level};signoff={signoff_id or 'unknown'}"
        if "operator" in satisfied and operator_approval is not None:
            meta += f";operator={operator_approval.approver}"

    return ExecutionVerdict(
        allowed=not reasons, action_class=action_class, approval_level=level,
        executor_id=executor_id, approval_metadata=meta,
        satisfied=tuple(satisfied), blocking_reasons=tuple(reasons))


def assert_executable(
    decision: Optional[TechLeadDecision],
    handoff: Optional[EngineerHandoff],
    request: ActionRequest,
    *,
    routing: Optional[GatewayRouting] = None,
    operator_approval: Optional[OperatorApproval] = None,
) -> ExecutionVerdict:
    """Authorize or RAISE :class:`ExecutionBlocked`. The hard chokepoint a real execution
    path calls right before it mutates anything."""

    verdict = authorize_execution(decision, handoff, request, routing=routing,
                                  operator_approval=operator_approval)
    if not verdict.allowed:
        raise ExecutionBlocked(verdict.blocking_reasons)
    return verdict


def bridge_to_autopilot(decision: TechLeadDecision) -> AutopilotDecision:
    """Adapt a lane signoff to the autopilot execution gate's :class:`TechLeadDecision`,
    so the REAL BoundedMutator path (`autopilot.validate_execution`) consumes it. A lane
    decision is autopilot-executable only when SIGNED_OFF at the internal-safe level."""

    can_exec = (decision.status == SIGNED_OFF
                and decision.approval_level == A.L2_INTERNAL_APPROVE)
    return AutopilotDecision(
        packet_summary=decision.pm_brief_ref,
        decision_class=decision.risk_class,
        approval_level=decision.approval_level,
        signoff_by=canonical_id(decision.signoff_by) or "tech-lead",
        can_execute=can_exec, rationale=decision.rationale)


def execution_commit_trailers(verdict: ExecutionVerdict, *, flow: str = "decision-lane",
                              env=None) -> Tuple[str, ...]:
    """Build the commit trailers binding the work to its approval (registry-backed).
    Only emitted for an ALLOWED verdict — no fabricated approval on a blocked path."""

    if not verdict.allowed:
        return ()
    return commit_trailers(
        verdict.executor_id, flow=flow, mode=verdict.action_class,
        handoff_from="tech-lead", handoff_to=verdict.executor_id,
        approval=verdict.approval_metadata, env=env)


def validate_execution_trailers(message: str, verdict: ExecutionVerdict) -> Tuple[str, ...]:
    """Reject a commit whose trailers don't carry the REAL approval metadata of *verdict*
    — no fake/absent approval on the work path. ``()`` = trailers match the authorization."""

    v = []
    text = message or ""
    if not verdict.allowed:
        v.append("commit: 승인되지 않은 실행 — 커밋 금지")
        return tuple(v)
    if f"Forgekit-Agent: {verdict.executor_id}" not in text:
        v.append(f"commit: executor 식별 트레일러 누락/불일치 (Forgekit-Agent: {verdict.executor_id})")
    if f"Forgekit-Approval: {verdict.approval_metadata}" not in text:
        v.append("commit: 승인 메타데이터 트레일러 누락/불일치 (Forgekit-Approval)")
    return tuple(v)


__all__ = (
    "SAFE", "RISKY", "DESTRUCTIVE", "ExecutionBlocked",
    "ActionRequest", "OperatorApproval", "ExecutionVerdict",
    "classify_action", "authorize_execution", "assert_executable",
    "bridge_to_autopilot", "execution_commit_trailers", "validate_execution_trailers",
)
