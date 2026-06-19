"""Safe-class execution validation + operator digest (repo-autopilot WT4).

Before any autopilot execution, :func:`validate_execution` re-checks the FULL gate
(internal signoff present + can_execute + diff/file/risk within limits) — execution
is refused otherwise. :func:`build_operator_digest` summarises a run for the operator
("아침 digest"): what was discovered, what auto-ran (internal-approved, no user), what
needs the USER, and what is blocked — so "내 승인 없이 가능" ≠ "아무 제약 없이 가능" is clear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

from .artifacts import ExecutionTaskSplit, TechLeadDecision
from .orchestrator import AutopilotLimits

# the fixed safe-class allowlist (code SSoT) — only these auto-execute (within chain)
SAFE_CLASS_ALLOWLIST: Tuple[str, ...] = (
    "docs", "tests", "lint", "format", "small-refactor", "note", "runbook",
    "ui-polish", "stale-packet-cleanup",
)
# never auto (mirrors approval L4 / selfimprove blocked)
AUTO_FORBIDDEN: Tuple[str, ...] = (
    "deploy", "infra-apply", "secret", "schema-migration", "broad-rewrite",
    "prod-config", "attack-flow",
)


def validate_execution(decision: TechLeadDecision, split: ExecutionTaskSplit,
                       limits: AutopilotLimits, *, diff: int = 0, files: int = 0,
                       risk: float = 0.0) -> Tuple[bool, Tuple[str, ...]]:
    """Re-check the full execution gate. Returns ``(allowed, reasons-if-not)``."""

    reasons: List[str] = []
    if decision is None or not decision.can_execute:
        reasons.append("tech-lead 내부 승인(can_execute) 없음")
    if decision is not None and decision.decision_class != "safe":
        reasons.append(f"safe class 아님({decision.decision_class}) — 자동 실행 금지")
    if diff > limits.max_diff:
        reasons.append(f"diff {diff} > 한도 {limits.max_diff}")
    if files > limits.max_files:
        reasons.append(f"files {files} > 한도 {limits.max_files}")
    if risk > limits.max_risk_score:
        reasons.append(f"risk {risk} > 한도 {limits.max_risk_score}")
    if not split.executor:
        reasons.append("executor 미지정")
    return (not reasons), tuple(reasons)


@dataclass
class OperatorDigest:
    """A morning-digest view of autopilot activity (operator-facing)."""

    discovered: int = 0
    auto_executed: List[dict] = field(default_factory=list)    # internal-approved, no user
    needs_user: List[dict] = field(default_factory=list)       # risky → user approval
    blocked: List[dict] = field(default_factory=list)          # restricted → runbook/operator

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "auto_executed": self.auto_executed,
            "needs_user": self.needs_user,
            "blocked": self.blocked,
        }

    def lines(self) -> Tuple[str, ...]:
        out = [
            "forgekit autopilot — operator digest",
            f"- 발견: {self.discovered}건",
            f"- 자동 실행(내부 승인만, 내 승인 불요): {len(self.auto_executed)}건",
            f"- 내 승인 필요(risky): {len(self.needs_user)}건",
            f"- 차단(restricted → runbook/operator): {len(self.blocked)}건",
            "주의: '내 승인 없이 가능' = 내부 PM→gateway→tech-lead 통과한 safe-class 뿐. "
            "deploy/secret/infra 는 절대 자동 아님.",
        ]
        return tuple(out)


def build_operator_digest(run_results: Sequence) -> OperatorDigest:
    """Aggregate autopilot run results into an operator digest."""

    digest = OperatorDigest()
    for res in run_results:
        digest.discovered += len(res.executed) + len(res.proposed)
        for e in res.executed:
            digest.auto_executed.append({"repo": res.repo, **e})
        for p in res.proposed:
            cls = p.get("decision_class", "")
            if cls == "blocked":
                digest.blocked.append({"repo": res.repo, **p})
            else:
                digest.needs_user.append({"repo": res.repo, **p})
    return digest


__all__ = (
    "SAFE_CLASS_ALLOWLIST", "AUTO_FORBIDDEN",
    "validate_execution", "OperatorDigest", "build_operator_digest",
)
