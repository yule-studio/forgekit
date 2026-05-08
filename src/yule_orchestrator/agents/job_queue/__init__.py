"""SQLite-backed job queue for the always-on engineering runtime.

This module is **additive**: no existing code path consumes it yet.
Phase A-M1 lays the foundation (schema + state machine + lease-based
``pick``) so subsequent migrations (A-M3 research worker, A-M4 role
worker) can move dispatch off the in-process gateway and onto a queue
without rewriting consumers each time.

Public surface:

- :class:`JobState` — 11-state lifecycle enum
- :class:`Job` — frozen dataclass mirroring one ``job_queue`` row
- :class:`JobQueue` — CRUD facade around the SQLite tables
- :data:`STATE_TRANSITIONS` — allowed transitions, validated on update

The queue lives in the same SQLite file as :mod:`storage.local_cache`
(``.cache/yule/cache.sqlite3`` by default, overridable via
``YULE_CACHE_DB_PATH``) so cross-table consistency stays under one
file lock + one WAL.
"""

from .approval_reply import (
    ApprovalIntent,
    ApprovalReplyOutcome,
    approval_to_obsidian_write_request,
    find_replyable_approval,
    handle_approval_reply,
    parse_approval_intent,
)
from .approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    APPROVAL_KIND_OBSIDIAN_WRITE,
    APPROVAL_KIND_RESEARCH_PROMOTION,
    ApprovalChannelResolver,
    ApprovalJobOutcome,
    ApprovalPostFn,
    ApprovalRequest,
    ApprovalWorker,
    JOB_TYPE_APPROVAL_POST,
    SERVICE_ID_APPROVAL_WORKER,
    SKIPPED_APPROVAL_CHANNEL_UNSET,
    SKIPPED_CLAIMED_BY_OTHER_WORKER,
    SKIPPED_DUPLICATE,
    env_approval_channel_resolver,
    render_approval_request,
)
from .heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    HeartbeatRecord,
    HeartbeatStore,
    SupervisorSweepReport,
    run_supervisor_sweep,
)
from .obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_DECISION,
    NOTE_KIND_KNOWLEDGE,
    NOTE_KIND_MEETING,
    NOTE_KIND_RESEARCH,
    NOTE_KIND_WORK_REPORT,
    ObsidianRenderError,
    ObsidianWriteJobOutcome,
    ObsidianWriteRequest,
    ObsidianWriterWorker,
    RenderNoteFn,
    SERVICE_ID_OBSIDIAN_WRITER,
    SKIPPED_APPROVAL_REQUIRED,
    SKIPPED_VAULT_UNAVAILABLE,
    VaultRootResolver,
    WriteNoteFn,
    default_render_fn,
    default_vault_root_resolver,
    default_write_fn,
)
from .research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
    SERVICE_ID_RESEARCH_WORKER,
    ResearchJobOutcome,
    ResearchWorker,
    RunnerCallable,
)
from .role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
    KIND_SYNTHESIS,
    KIND_TURN,
    RoleTakeJobOutcome,
    RoleTakeRunner,
    RoleTakeWorker,
    SERVICE_ID_ROLE_WORKER_PREFIX,
    service_id_for_role,
)
from .state_machine import (
    JobState,
    STATE_TRANSITIONS,
    TERMINAL_STATES,
    is_terminal,
    validate_transition,
)
from .store import (
    Job,
    JobQueue,
    JobQueueError,
    QueueDatabaseError,
)


__all__ = (
    "APPROVAL_KIND_ENGINEERING_WRITE",
    "APPROVAL_KIND_OBSIDIAN_WRITE",
    "APPROVAL_KIND_RESEARCH_PROMOTION",
    "ApprovalChannelResolver",
    "ApprovalIntent",
    "ApprovalJobOutcome",
    "ApprovalPostFn",
    "ApprovalReplyOutcome",
    "ApprovalRequest",
    "ApprovalWorker",
    "DEFAULT_HEARTBEAT_DEADLINE_SECONDS",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "HeartbeatRecord",
    "HeartbeatStore",
    "JOB_TYPE_APPROVAL_POST",
    "JOB_TYPE_OBSIDIAN_WRITE",
    "JOB_TYPE_RESEARCH_COLLECT",
    "JOB_TYPE_ROLE_TAKE",
    "Job",
    "JobQueue",
    "JobQueueError",
    "JobState",
    "KIND_OPEN",
    "KIND_SYNTHESIS",
    "KIND_TURN",
    "NOTE_KIND_DECISION",
    "NOTE_KIND_KNOWLEDGE",
    "NOTE_KIND_MEETING",
    "NOTE_KIND_RESEARCH",
    "NOTE_KIND_WORK_REPORT",
    "ObsidianRenderError",
    "ObsidianWriteJobOutcome",
    "ObsidianWriteRequest",
    "ObsidianWriterWorker",
    "QueueDatabaseError",
    "RenderNoteFn",
    "ResearchJobOutcome",
    "ResearchWorker",
    "RoleTakeJobOutcome",
    "RoleTakeRunner",
    "RoleTakeWorker",
    "RunnerCallable",
    "SERVICE_ID_APPROVAL_WORKER",
    "SERVICE_ID_OBSIDIAN_WRITER",
    "SERVICE_ID_RESEARCH_WORKER",
    "SERVICE_ID_ROLE_WORKER_PREFIX",
    "SKIPPED_APPROVAL_CHANNEL_UNSET",
    "SKIPPED_APPROVAL_REQUIRED",
    "SKIPPED_CLAIMED_BY_OTHER_WORKER",
    "SKIPPED_DUPLICATE",
    "SKIPPED_VAULT_UNAVAILABLE",
    "STATE_TRANSITIONS",
    "SupervisorSweepReport",
    "TERMINAL_STATES",
    "VaultRootResolver",
    "WriteNoteFn",
    "default_render_fn",
    "default_vault_root_resolver",
    "default_write_fn",
    "env_approval_channel_resolver",
    "is_terminal",
    "render_approval_request",
    "run_supervisor_sweep",
    "service_id_for_role",
    "validate_transition",
)
