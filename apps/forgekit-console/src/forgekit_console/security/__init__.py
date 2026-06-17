"""Red/blue security mode (WT5) — own-assets-only, plan-first, approval-gated."""

from __future__ import annotations

from .contract import (
    DRILL_APPROVED_ACTIVE,
    DRILL_BLOCKED,
    DRILL_PLAN_ONLY,
    AttackPlan,
    DefenseRunbook,
    FindingsDigest,
    SecurityDrillPacket,
    TargetSpec,
)
from .drill import build_drill, k3s_isolation_runbook, resolve_target, synthesize_purple

__all__ = (
    "DRILL_PLAN_ONLY", "DRILL_BLOCKED", "DRILL_APPROVED_ACTIVE",
    "TargetSpec", "AttackPlan", "DefenseRunbook", "FindingsDigest", "SecurityDrillPacket",
    "build_drill", "resolve_target", "synthesize_purple", "k3s_isolation_runbook",
)
