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
    NEEDS_INFO,
    SIGNED_OFF,
    DISSENT_STANCES,
    ConsultNote,
    EngineerHandoff,
    MeetingRecord,
    ParticipantPosition,
    PMBrief,
    RejectedOption,
    SpecialistBriefing,
    StackComparison,
    StackOption,
    TechLeadDecision,
)
from .validators import (
    validate_consult,
    validate_handoff,
    validate_meeting,
    validate_pm_brief,
    validate_specialist_briefing,
    validate_stack_comparison,
    validate_tech_lead_decision,
)
from .lane import (
    GatewayRouting,
    LaneResult,
    build_specialist_briefing,
    can_engineer_start,
    can_specialist_start,
    handoff_to_engineer,
    route_to_tech_lead,
    run_lane,
    tech_lead_decide,
    tech_lead_request_more_info,
)
from .gateway import (
    GATEWAY_APPROVE,
    GATEWAY_REJECT,
    GATEWAY_REQUEST_INFO,
    GATEWAY_VERDICTS,
    GatewayPacket,
    gateway_review,
    validate_gateway_packet,
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
from .readiness import (
    STAGE_DECISION_PENDING,
    STAGE_EXECUTABLE,
    STAGE_HANDOFF_PENDING,
    STAGE_MEETING_PENDING,
    STAGE_NO_PM_BRIEF,
    STAGE_ORDER,
    LaneReadiness,
    assess_lane_readiness,
)
from .decision_log import (
    EVENT_KINDS,
    KIND_APPROVAL,
    KIND_BRIEF,
    KIND_CONSULT,
    KIND_DECISION,
    KIND_EXECUTION,
    KIND_GATEWAY,
    KIND_HANDOFF,
    KIND_MEETING,
    GovernanceEvent,
    decision_trail_from_log,
    governance_log_path,
    readiness_from_log,
    record_governance_event,
    record_lane_artifacts,
    replay_governance_log,
)

__all__ = (
    # schemas
    "PMBrief", "StackOption", "StackComparison", "ConsultNote", "ParticipantPosition",
    "MeetingRecord", "TechLeadDecision", "EngineerHandoff",
    "RejectedOption", "SpecialistBriefing",
    "DRAFT", "SIGNED_OFF", "CONDITIONAL", "BLOCKED", "ESCALATED", "NEEDS_INFO", "DISSENT_STANCES",
    # validators
    "validate_pm_brief", "validate_stack_comparison", "validate_consult", "validate_meeting",
    "validate_tech_lead_decision", "validate_handoff", "validate_specialist_briefing",
    # lane
    "GatewayRouting", "LaneResult", "route_to_tech_lead", "tech_lead_decide",
    "handoff_to_engineer", "can_engineer_start", "build_specialist_briefing",
    "can_specialist_start", "run_lane", "tech_lead_request_more_info",
    # gateway packet (approve / reject / request-more-info)
    "GATEWAY_APPROVE", "GATEWAY_REJECT", "GATEWAY_REQUEST_INFO", "GATEWAY_VERDICTS",
    "GatewayPacket", "gateway_review", "validate_gateway_packet",
    # runtime enforcement
    "SAFE", "RISKY", "DESTRUCTIVE", "ExecutionBlocked",
    "ActionRequest", "OperatorApproval", "ExecutionVerdict",
    "classify_action", "authorize_execution", "assert_executable",
    "authorize_runtime_execution", "make_runtime_authorizer",
    "bridge_to_autopilot", "execution_commit_trailers", "validate_execution_trailers",
    # readiness gate
    "STAGE_NO_PM_BRIEF", "STAGE_MEETING_PENDING", "STAGE_DECISION_PENDING",
    "STAGE_HANDOFF_PENDING", "STAGE_EXECUTABLE", "STAGE_ORDER",
    "LaneReadiness", "assess_lane_readiness",
    # replay-able decision log
    "KIND_BRIEF", "KIND_CONSULT", "KIND_GATEWAY", "KIND_MEETING", "KIND_DECISION",
    "KIND_APPROVAL", "KIND_HANDOFF", "KIND_EXECUTION", "EVENT_KINDS", "GovernanceEvent",
    "governance_log_path", "record_governance_event", "replay_governance_log",
    "record_lane_artifacts", "readiness_from_log", "decision_trail_from_log",
)
