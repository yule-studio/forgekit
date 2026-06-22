"""Self-improvement (WT4) — repo gaps → risk-classified improvement packets (bounded)."""

from __future__ import annotations

from .packet import (
    RISK_BLOCKED,
    RISK_RISKY,
    RISK_SAFE,
    RepoImprovementPacket,
    classify_risk,
    make_packet,
)
from .loop import SelfImprovementResult, route_packet, run_self_improvement
from .execute_bridge import (
    OUTCOME_AWAITING,
    OUTCOME_BLOCKED,
    OUTCOME_ERROR,
    OUTCOME_EXECUTED,
    ExecuteOutcome,
    build_execution_commit_message,
    execute_approved_packet,
)

__all__ = (
    "RISK_SAFE", "RISK_RISKY", "RISK_BLOCKED",
    "RepoImprovementPacket", "classify_risk", "make_packet",
    "SelfImprovementResult", "run_self_improvement", "route_packet",
    # GW4-B execution bridge
    "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING", "OUTCOME_ERROR",
    "ExecuteOutcome", "execute_approved_packet", "build_execution_commit_message",
)
