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

__all__ = (
    "RISK_SAFE", "RISK_RISKY", "RISK_BLOCKED",
    "RepoImprovementPacket", "classify_risk", "make_packet",
    "SelfImprovementResult", "run_self_improvement", "route_packet",
)
