"""Engineering-agent department runtime: runners, registry, hooks."""

from .messaging.dispatcher import (
    DispatchPlan,
    DispatchRequest,
    Dispatcher,
    RoleAssignment,
    TaskType,
    render_plan_summary,
)
from .messaging.message import (
    AgentMessage,
    ContextRef,
    Priority,
    RequestedAction,
    close_thread,
    new_request,
    reply_to,
    role_address,
    with_thread_id,
)
from .messaging.registry import (
    DEFAULT_RUNNER_FACTORIES,
    ParticipantsPool,
    RegistryError,
    RunnerFactory,
    build_participants_pool,
)
from .workflow import (
    CompletionResult,
    IntakeResult,
    ProgressResult,
    WorkflowError,
    WorkflowOrchestrator,
    extract_urls,
    format_completion_message,
    format_intake_message,
    format_progress_message,
)
from .workflow_state import WorkflowSession, WorkflowState

__all__ = [
    "AgentMessage",
    "CompletionResult",
    "ContextRef",
    "DEFAULT_RUNNER_FACTORIES",
    "DispatchPlan",
    "DispatchRequest",
    "Dispatcher",
    "IntakeResult",
    "ParticipantsPool",
    "Priority",
    "ProgressResult",
    "RegistryError",
    "RequestedAction",
    "RoleAssignment",
    "RunnerFactory",
    "TaskType",
    "WorkflowError",
    "WorkflowOrchestrator",
    "WorkflowSession",
    "WorkflowState",
    "build_participants_pool",
    "close_thread",
    "extract_urls",
    "format_completion_message",
    "format_intake_message",
    "format_progress_message",
    "new_request",
    "render_plan_summary",
    "reply_to",
    "role_address",
    "with_thread_id",
]
