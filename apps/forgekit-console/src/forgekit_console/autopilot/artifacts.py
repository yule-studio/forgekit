"""Shared multi-agent artifact contracts (repo-autopilot WT1).

The team passes typed artifacts down the chain: RepoFinding → PMPacket → GatewayRoute
→ TechLeadDecision → ExecutionTaskSplit → VerificationReport, with a VaultTraceNote
recording who/why/what/approval. Pure dataclasses → serialisable + testable. A
specialist may only act on a TechLeadDecision (enforced in :mod:`autopilot.chain`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class RepoFinding:
    repo: str
    finding: str
    kind: str = "gap"            # gap / discomfort / test / lint / docs / ops
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"repo": self.repo, "finding": self.finding, "kind": self.kind,
                "evidence": self.evidence}


@dataclass(frozen=True)
class PMPacket:
    finding: RepoFinding
    why_it_matters: str = ""
    user_value: str = ""
    missing: Tuple[str, ...] = ()
    recommended_owner: str = "tech-lead"

    def to_dict(self) -> dict:
        return {"finding": self.finding.to_dict(), "why_it_matters": self.why_it_matters,
                "user_value": self.user_value, "missing": list(self.missing),
                "recommended_owner": self.recommended_owner}


@dataclass(frozen=True)
class GatewayRoute:
    packet_summary: str
    owner_role: str
    route_reason: str = ""

    def to_dict(self) -> dict:
        return {"packet_summary": self.packet_summary, "owner_role": self.owner_role,
                "route_reason": self.route_reason}


@dataclass(frozen=True)
class TechLeadDecision:
    packet_summary: str
    decision_class: str          # safe / risky / blocked
    approval_level: str          # autopilot.approval L*
    signoff_by: str = "tech-lead"
    can_execute: bool = False     # True only for internal-approved safe class
    rationale: str = ""

    def to_dict(self) -> dict:
        return {"packet_summary": self.packet_summary, "decision_class": self.decision_class,
                "approval_level": self.approval_level, "signoff_by": self.signoff_by,
                "can_execute": self.can_execute, "rationale": self.rationale}


@dataclass(frozen=True)
class ExecutionTaskSplit:
    decision_summary: str
    executor: str                # the SINGLE role holding execution rights
    tasks: Tuple[str, ...] = ()
    diff_limit: int = 0
    file_limit: int = 0

    def to_dict(self) -> dict:
        return {"decision_summary": self.decision_summary, "executor": self.executor,
                "tasks": list(self.tasks), "diff_limit": self.diff_limit,
                "file_limit": self.file_limit}


@dataclass(frozen=True)
class VerificationReport:
    task_summary: str
    passed: bool = False
    checks: Tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict:
        return {"task_summary": self.task_summary, "passed": self.passed,
                "checks": list(self.checks), "note": self.note}


@dataclass(frozen=True)
class VaultTraceNote:
    who: str
    why: str
    what: str
    approval_chain: Tuple[str, ...] = ()   # the phases/levels traversed
    area: str = ""

    def to_dict(self) -> dict:
        return {"who": self.who, "why": self.why, "what": self.what,
                "approval_chain": list(self.approval_chain), "area": self.area}


__all__ = (
    "RepoFinding", "PMPacket", "GatewayRoute", "TechLeadDecision",
    "ExecutionTaskSplit", "VerificationReport", "VaultTraceNote",
)
