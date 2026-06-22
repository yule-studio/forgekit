"""PM / Tech-Lead decision lane — schemas + anti-fake validators + handoff rules.

The design-decision sibling of the autopilot finding-chain: a real PM brief, a recorded
meeting (no rubber-stamp consensus), a stack comparison/recommendation, a tech-lead
signoff that fixes design-system + coding-convention + stack + tradeoff + approval, and a
single-executor engineer handoff. "fake meeting / fake signoff" never reaches execution —
the gate is :func:`can_engineer_start` / :func:`run_lane`.

Docs SSoT: ``docs/pm-techlead-lane.md``.
"""

from __future__ import annotations

from .schemas import (
    BLOCKED,
    CONDITIONAL,
    DRAFT,
    ESCALATED,
    SIGNED_OFF,
    DISSENT_STANCES,
    EngineerHandoff,
    MeetingRecord,
    ParticipantPosition,
    PMBrief,
    StackComparison,
    StackOption,
    TechLeadDecision,
)
from .validators import (
    validate_handoff,
    validate_meeting,
    validate_pm_brief,
    validate_stack_comparison,
    validate_tech_lead_decision,
)
from .lane import (
    GatewayRouting,
    LaneResult,
    can_engineer_start,
    handoff_to_engineer,
    route_to_tech_lead,
    run_lane,
    tech_lead_decide,
)
from .enforcement import (
    DESTRUCTIVE,
    RISKY,
    SAFE,
    ActionRequest,
    ExecutionBlocked,
    ExecutionVerdict,
    OperatorApproval,
    assert_executable,
    authorize_execution,
    authorize_runtime_execution,
    bridge_to_autopilot,
    classify_action,
    execution_commit_trailers,
    make_runtime_authorizer,
    validate_execution_trailers,
)

__all__ = (
    # schemas
    "PMBrief", "StackOption", "StackComparison", "ParticipantPosition",
    "MeetingRecord", "TechLeadDecision", "EngineerHandoff",
    "DRAFT", "SIGNED_OFF", "CONDITIONAL", "BLOCKED", "ESCALATED", "DISSENT_STANCES",
    # validators
    "validate_pm_brief", "validate_stack_comparison", "validate_meeting",
    "validate_tech_lead_decision", "validate_handoff",
    # lane
    "GatewayRouting", "LaneResult", "route_to_tech_lead", "tech_lead_decide",
    "handoff_to_engineer", "can_engineer_start", "run_lane",
    # runtime enforcement
    "SAFE", "RISKY", "DESTRUCTIVE", "ExecutionBlocked",
    "ActionRequest", "OperatorApproval", "ExecutionVerdict",
    "classify_action", "authorize_execution", "assert_executable",
    "authorize_runtime_execution", "make_runtime_authorizer",
    "bridge_to_autopilot", "execution_commit_trailers", "validate_execution_trailers",
)
