"""repo-autopilot (WT1) — internal approval chain + shared multi-agent artifacts.

"User 승인 없음"은 가능하지만 "internal 승인 없음"은 불가 — 모든 실행은 PM→gateway→
tech-lead 체계를 거친 TechLeadDecision 위에서만.
"""

from __future__ import annotations

from .approval import (
    L0_COLLECT,
    L1_PROPOSE,
    L2_INTERNAL_APPROVE,
    L3_USER_APPROVE,
    L4_RESTRICTED,
    autopilot_can_execute,
    classify_level,
    needs_user,
)
from .artifacts import (
    ExecutionTaskSplit,
    GatewayRoute,
    PMPacket,
    RepoFinding,
    TechLeadDecision,
    VaultTraceNote,
    VerificationReport,
)
from .chain import (
    can_specialist_execute,
    gateway_route,
    pm_structure,
    run_internal_chain,
    tech_lead_signoff,
    trace_note,
)
from .observe import (
    UIReferenceState,
    default_ui_reference,
    observe_repo,
    to_improvement_packets,
)
from .orchestrator import (
    DEFAULT_ALLOWLIST,
    AutopilotLimits,
    AutopilotOrchestrator,
    AutopilotRunResult,
    ExecutorArbiter,
)

__all__ = (
    "L0_COLLECT", "L1_PROPOSE", "L2_INTERNAL_APPROVE", "L3_USER_APPROVE", "L4_RESTRICTED",
    "autopilot_can_execute", "classify_level", "needs_user",
    "RepoFinding", "PMPacket", "GatewayRoute", "TechLeadDecision",
    "ExecutionTaskSplit", "VerificationReport", "VaultTraceNote",
    "run_internal_chain", "pm_structure", "gateway_route", "tech_lead_signoff",
    "can_specialist_execute", "trace_note",
    "UIReferenceState", "default_ui_reference", "observe_repo", "to_improvement_packets",
    "DEFAULT_ALLOWLIST", "AutopilotLimits", "AutopilotOrchestrator",
    "AutopilotRunResult", "ExecutorArbiter",
)
