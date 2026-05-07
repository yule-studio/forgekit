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

from .heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    HeartbeatRecord,
    HeartbeatStore,
    SupervisorSweepReport,
    run_supervisor_sweep,
)
from .research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
    SERVICE_ID_RESEARCH_WORKER,
    ResearchJobOutcome,
    ResearchWorker,
    RunnerCallable,
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
    "DEFAULT_HEARTBEAT_DEADLINE_SECONDS",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "HeartbeatRecord",
    "HeartbeatStore",
    "JOB_TYPE_RESEARCH_COLLECT",
    "Job",
    "JobQueue",
    "JobQueueError",
    "JobState",
    "QueueDatabaseError",
    "ResearchJobOutcome",
    "ResearchWorker",
    "RunnerCallable",
    "SERVICE_ID_RESEARCH_WORKER",
    "STATE_TRANSITIONS",
    "SupervisorSweepReport",
    "TERMINAL_STATES",
    "is_terminal",
    "run_supervisor_sweep",
    "validate_transition",
)
