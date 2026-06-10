"""Runtime status surface — A-M6.3.

Read-only snapshot of the always-on engineering runtime so an
operator can see "is everything alive?" with one CLI invocation:

  yule runtime status --profile engineering [--json]

Reports four things, each derivable from the heartbeat store +
job queue + autonomy journal without touching Discord or restart
parents:

  * Per-service health — heartbeat age vs. deadline, with the
    inventory entries (``ENGINEERING_PROFILE``) annotated as
    ``alive`` / ``stale`` / ``unknown`` / ``reserved``.
  * Per-job-type queue counts — how many rows per state, oldest
    queued age, so a stuck queue is visible.
  * Recent failures — the most recent ``FAILED_RETRYABLE`` /
    ``FAILED_TERMINAL`` rows with their one-line error string so
    the operator can decide whether to requeue or escalate.
  * Autonomy producer/funnel surface — last few autonomy ticks +
    completion-funnel decisions so operators understand what the
    runtime *just decided to do next* (Round 4 of #73). The data
    flows from the in-process :class:`RuntimeAutonomyJournal` that
    the supervisor populates after every producer tick / funnel
    completion.

Out of scope (per A-M6.3 spec): supervisor restart counters
(in-process only, not persisted), ``#봇-상태`` Discord broadcast,
M7 fallback / circuit-break / degrade.

Module layout — this module owns the **builder / core** axis: the
report dataclasses, the health / outcome constants, and the
``build_*`` data-assembly functions. The other three axes were
split into sibling modules and re-exported at the bottom of this
file so existing ``from .status import ...`` importers stay
unchanged:

  * :mod:`runtime.status_render` — text / JSON / compact / markdown
    renderers + formatting helpers + the live-smoke checklist.
  * :mod:`runtime.status_operator_actions` — the ``OperatorAction``
    surface (``#승인-대기`` / queue triage) + compact summary.
  * :mod:`runtime.status_journal` — the process-local autonomy
    journal ring buffer + default singleton.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

from ..agents.job_queue.heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    HeartbeatStore,
)
from ..agents.job_queue.state_machine import JobState
from ..agents.job_queue.store import Job, JobQueue
from yule_runtime.services import ServiceKind, ServiceSpec, list_services


# Health labels surfaced in renderer output. String constants so the
# ``--json`` consumer can match exact values without enum import.
HEALTH_ALIVE: str = "alive"
HEALTH_STALE: str = "stale"
HEALTH_UNKNOWN: str = "unknown"
HEALTH_RESERVED: str = "reserved"
# P0-T — graceful-disable 와 unknown 을 구분. operator 가 "토큰만 빠졌나"
# vs "프로세스 미기동" 을 즉시 식별할 수 있어야 한다.
HEALTH_GRACEFUL_DISABLED: str = "graceful_disabled"
# A-M7-final: circuit-open trumps the heartbeat-based labels because
# the supervisor parent has explicitly stopped restarting the service.
# A live ``alive`` heartbeat could otherwise mask the fact that the
# breaker is keeping the worker offline.
HEALTH_CIRCUIT_OPEN: str = "circuit_open"


# Map ServiceKind → expected job_type. ``None`` means "not a queue
# consumer" (supervisor watcher / Discord gateway). Used to annotate
# the per-service queue context in the report.
_KIND_TO_JOB_TYPE: Mapping[ServiceKind, Optional[str]] = {
    ServiceKind.RESEARCH_WORKER: "research_collect",
    ServiceKind.ROLE_WORKER: "role_take",
    ServiceKind.APPROVAL_WORKER: "approval_post",
    ServiceKind.OBSIDIAN_WRITER: "obsidian_write",
    ServiceKind.CODING_EXECUTOR: "coding_execute",
    # P0-T — github_work_order 큐 consumer 가 status 표면에 jobs queued
    # /in_progress/saved 카운트와 함께 보이도록 매핑.
    ServiceKind.GITHUB_WORK_ORDER_EXECUTOR: "github_work_order",
    ServiceKind.SUPERVISOR: None,
    ServiceKind.DISCORD_GATEWAY: None,
    ServiceKind.RESERVED_DISCORD_GATEWAY: None,
}


# Job types we always surface in the queue summary, even when the row
# count is zero, so operators can distinguish "executor up + nothing to
# do" from "this job type isn't wired at all". Reflects the canonical
# 4-worker set this runtime ships with.
_ALWAYS_VISIBLE_JOB_TYPES: Tuple[str, ...] = (
    "research_collect",
    "role_take",
    "approval_post",
    "obsidian_write",
    "coding_execute",
)


@dataclass(frozen=True)
class ServiceStatus:
    """One service row in the report."""

    service_id: str
    kind: str
    role: Optional[str]
    description: str
    implemented: bool
    health: str
    heartbeat_age_seconds: Optional[float]
    heartbeat_last_beat: Optional[float]
    pid: Optional[int]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    job_type: Optional[str] = None


@dataclass(frozen=True)
class JobTypeSummary:
    """Per-job-type queue snapshot."""

    job_type: str
    queued: int
    in_progress: int
    saved: int
    failed_retryable: int
    failed_terminal: int
    oldest_queued_age_seconds: Optional[float]


@dataclass(frozen=True)
class FailedJobSummary:
    """One recent failed row, condensed for status display."""

    job_id: str
    job_type: str
    role: Optional[str]
    state: str
    attempt: int
    age_seconds: float
    error: Optional[str]


# ---------------------------------------------------------------------------
# Autonomy producer / completion funnel surface — Round 4 of #73.
# ---------------------------------------------------------------------------
#
# The runtime can now generate its own follow-up work (autonomy
# producer) and decide whether to advance the queue after every
# completion (completion funnel). Operators must see those decisions
# alongside the heartbeat / queue snapshot, otherwise "the agent did
# nothing" and "the agent decided to wait for approval" look identical
# from the outside.
#
# These dataclasses are intentionally narrow projections of the full
# producer/funnel objects — only the fields an operator needs in a
# Discord post / `yule runtime status` line. Full reports stay in
# memory for diagnostics; the journal trims to a small ring buffer so
# a long-running supervisor never grows unbounded.

# Outcome strings the autonomy producer emits per dispatch. Mirrored
# here so the renderer can ladder them into operator-friendly labels
# (and tests don't import :mod:`agents.job_queue.autonomy_producer`).
AUTONOMY_OUTCOME_DISPATCHED: str = "dispatched"
AUTONOMY_OUTCOME_DEDUPED: str = "deduped"
AUTONOMY_OUTCOME_LOCKED: str = "locked_by_other"
AUTONOMY_OUTCOME_SKIPPED: str = "skipped"
AUTONOMY_OUTCOME_ERROR: str = "error"


@dataclass(frozen=True)
class AutonomyDispatchSummary:
    """One scheduling decision projected for the status surface.

    Mirrors the operator-relevant subset of
    :class:`agents.job_queue.autonomy_producer.AutonomyDispatch` so
    the status report stays usable without importing the producer.
    """

    source: str
    outcome: str
    session_id: str = ""
    executor_role: str = ""
    job_id: Optional[str] = None
    branch_hint: str = ""
    reason: str = ""


@dataclass(frozen=True)
class AutonomyTickSummary:
    """One autonomy producer tick condensed for the status report."""

    tick_id: str
    started_at: str
    finished_at: str
    next_task_source: Optional[str]
    summary_line: str
    dispatches: Tuple[AutonomyDispatchSummary, ...] = ()
    locks_held: Tuple[str, ...] = ()
    error: Optional[str] = None

    def has_actionable_signal(self) -> bool:
        """True when the operator should pay attention to this tick.

        Either the tick errored, an individual dispatch errored / was
        locked-by-other (a sign the lock-holder may have crashed), or
        the tick reported a CI-failed source the funnel could not
        advance.
        """

        if self.error:
            return True
        for d in self.dispatches:
            if d.outcome in {AUTONOMY_OUTCOME_ERROR, AUTONOMY_OUTCOME_LOCKED}:
                return True
        return False


@dataclass(frozen=True)
class CompletionFunnelSummary:
    """One completion-funnel decision lifted off ``session.extra``.

    Populated by :func:`runtime.status_poster.collect_recent_completion_funnel`
    (read-only walk of recent sessions) and surfaced in the report so
    operators can see "last X jobs ended in needs_approval" without
    cracking open SQLite.
    """

    session_id: str
    job_id: str
    job_type: str
    completion_status: str  # done / blocked / needs_approval / retry_ready
    ticked: bool
    reason: str = ""
    recommended_source: Optional[str] = None
    producer_summary: Optional[str] = None
    at: str = ""

    def is_actionable(self) -> bool:
        """True when this funnel row demands operator attention.

        ``blocked`` and ``needs_approval`` deliberately don't tick the
        producer — but the operator must know the runtime is parked.
        """

        return self.completion_status in (
            "blocked",
            "needs_approval",
        )


@dataclass(frozen=True)
class CodingDispatchSummary:
    """Approved-coding sessions waiting for the dispatcher.

    ``ready_sessions`` counts sessions whose ``coding_proposal`` has
    been promoted to ``coding_job=ready`` AND whose ``coding_execute``
    dispatch hasn't fired yet. ``dispatched_sessions`` counts the ones
    that already have a dispatch marker (so operators can confirm the
    producer tick is actually running).

    Used by operators to tell apart the two failure modes the user
    hit on session ``3163b5cf6c9b``:

    * executor ALIVE + 0 ready / 0 dispatched → no eligible work
    * executor ALIVE + N>0 ready / 0 dispatched → producer tick missing
    * executor ALIVE + 0 ready / N>0 dispatched → all caught up
    """

    ready_sessions: int = 0
    dispatched_sessions: int = 0
    sample_session_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeStatusReport:
    """Top-level snapshot returned by :func:`build_runtime_status`."""

    profile: str
    generated_at: float
    deadline_seconds: float
    services: Tuple[ServiceStatus, ...]
    job_types: Tuple[JobTypeSummary, ...]
    failed_recent: Tuple[FailedJobSummary, ...]
    warnings: Tuple[str, ...]
    autonomy_recent: Tuple[AutonomyTickSummary, ...] = ()
    completion_funnel_recent: Tuple[CompletionFunnelSummary, ...] = ()
    autonomy_locks_held: Tuple[str, ...] = ()
    coding_dispatch: CodingDispatchSummary = CodingDispatchSummary()


# States the renderer treats as "in flight" (worker has the row but
# it's not done yet). Kept here so a future state addition only
# touches one tuple.
_IN_PROGRESS_STATES: Tuple[JobState, ...] = (
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
    JobState.WAITING_FOR_ROLE,
)


def build_runtime_status(
    *,
    profile: str = "engineering",
    queue: JobQueue,
    heartbeats: HeartbeatStore,
    deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    failed_limit: int = 10,
    now: Optional[float] = None,
    circuit_snapshots: Optional[Mapping[str, Any]] = None,
    autonomy_journal: Optional["RuntimeAutonomyJournal"] = None,
    autonomy_recent_limit: int = 5,
    completion_funnel_recent: Sequence[CompletionFunnelSummary] = (),
) -> RuntimeStatusReport:
    """Snapshot the runtime into a :class:`RuntimeStatusReport`.

    Pure read — no SQL writes, no state transitions. The function
    consults the inventory + heartbeat store + queue read APIs +
    autonomy journal and returns a frozen report the renderer can
    stringify.

    *autonomy_journal* defaults to :func:`get_default_autonomy_journal`
    so the supervisor's tick-recorded data appears automatically.
    Tests pass an isolated journal to avoid cross-test leakage.
    *completion_funnel_recent* is a caller-prepared list of recent
    funnel decisions (typically built by
    :func:`runtime.status_poster.collect_recent_completion_funnel`)
    so the status builder doesn't need to import the workflow cache
    layer directly.
    """

    now_ts = now if now is not None else time.time()
    specs = list_services(profile)

    heartbeat_index: dict[str, Any] = {
        record.service_id: record for record in heartbeats.list_all()
    }

    circuit_map = dict(circuit_snapshots) if circuit_snapshots else {}
    services: list[ServiceStatus] = []
    for spec in specs:
        svc = _build_service_status(
            spec=spec,
            heartbeat=heartbeat_index.get(spec.service_id),
            deadline_seconds=deadline_seconds,
            now_ts=now_ts,
        )
        # A-M7-final: persisted circuit-open trumps heartbeat health.
        # Operator must see "supervisor stopped this on purpose"
        # rather than a green "alive" line.
        snap = circuit_map.get(spec.service_id)
        if snap is not None and getattr(snap, "is_open", False):
            svc = ServiceStatus(
                service_id=svc.service_id,
                kind=svc.kind,
                role=svc.role,
                description=svc.description,
                implemented=svc.implemented,
                health=HEALTH_CIRCUIT_OPEN,
                heartbeat_age_seconds=svc.heartbeat_age_seconds,
                heartbeat_last_beat=svc.heartbeat_last_beat,
                pid=svc.pid,
                metadata=dict(svc.metadata),
                job_type=svc.job_type,
            )
        services.append(svc)

    job_types = _build_job_type_summaries(queue=queue, now_ts=now_ts)
    failed_recent = _build_failed_recent(
        queue=queue, limit=failed_limit, now_ts=now_ts
    )

    journal = autonomy_journal if autonomy_journal is not None else _DEFAULT_JOURNAL
    autonomy_recent = journal.recent(limit=max(0, int(autonomy_recent_limit)))
    locks_held = journal.locks_held()
    funnel_recent = tuple(completion_funnel_recent)

    warnings = _build_warnings(
        services=services,
        job_types=job_types,
        failed_recent=failed_recent,
        autonomy_recent=autonomy_recent,
        completion_funnel_recent=funnel_recent,
    )

    coding_dispatch = _build_coding_dispatch_summary()

    return RuntimeStatusReport(
        profile=profile,
        generated_at=now_ts,
        deadline_seconds=float(deadline_seconds),
        services=tuple(services),
        job_types=tuple(job_types),
        failed_recent=tuple(failed_recent),
        warnings=tuple(warnings),
        autonomy_recent=autonomy_recent,
        completion_funnel_recent=funnel_recent,
        autonomy_locks_held=locks_held,
        coding_dispatch=coding_dispatch,
    )


def _build_coding_dispatch_summary() -> CodingDispatchSummary:
    """Count approved-coding sessions waiting for / already-dispatched to executor.

    Best-effort: any exception from the workflow store falls back to a
    zero summary so the rest of the status report still renders. The
    operator surface treats "no data" as "no signal" rather than as
    "executor up + no work" — see ``_build_warnings`` for the explicit
    warning thresholds.
    """

    try:
        from ..agents.job_queue.coding_execute_dispatcher import iter_ready_coding_jobs
    except Exception:  # noqa: BLE001 - missing module shouldn't crash status
        return CodingDispatchSummary()

    ready_ids: list[str] = []
    dispatched_ids: list[str] = []
    try:
        for ready in iter_ready_coding_jobs(include_dispatched=True):
            session_id = str(ready.session_id or "")
            if not session_id:
                continue
            if ready.has_been_dispatched():
                dispatched_ids.append(session_id)
            else:
                ready_ids.append(session_id)
    except Exception:  # noqa: BLE001 - cache hiccup shouldn't crash status
        return CodingDispatchSummary()

    sample = tuple(ready_ids[:5]) if ready_ids else tuple(dispatched_ids[:5])
    return CodingDispatchSummary(
        ready_sessions=len(ready_ids),
        dispatched_sessions=len(dispatched_ids),
        sample_session_ids=sample,
    )


def _build_service_status(
    *,
    spec: ServiceSpec,
    heartbeat: Any,
    deadline_seconds: float,
    now_ts: float,
) -> ServiceStatus:
    job_type = _KIND_TO_JOB_TYPE.get(spec.kind)

    if not spec.is_implemented():
        return ServiceStatus(
            service_id=spec.service_id,
            kind=spec.kind.value,
            role=spec.role,
            description=spec.description,
            implemented=False,
            health=HEALTH_RESERVED,
            heartbeat_age_seconds=None,
            heartbeat_last_beat=None,
            pid=None,
            metadata={},
            job_type=job_type,
        )

    if heartbeat is None:
        return ServiceStatus(
            service_id=spec.service_id,
            kind=spec.kind.value,
            role=spec.role,
            description=spec.description,
            implemented=True,
            health=HEALTH_UNKNOWN,
            heartbeat_age_seconds=None,
            heartbeat_last_beat=None,
            pid=None,
            metadata={},
            job_type=job_type,
        )

    age = max(0.0, now_ts - float(heartbeat.last_beat))
    metadata = dict(heartbeat.metadata or {})
    # P0-T — heartbeat metadata 의 state 가 graceful_disabled 면 ALIVE
    # 가 아니라 별도 분류. operator hint 도 token/env 안내로 분기.
    state = str(metadata.get("state") or "").strip().lower()
    if state == "graceful_disabled":
        health = HEALTH_GRACEFUL_DISABLED
    else:
        health = (
            HEALTH_ALIVE
            if age <= max(1.0, float(deadline_seconds))
            else HEALTH_STALE
        )
    return ServiceStatus(
        service_id=spec.service_id,
        kind=spec.kind.value,
        role=spec.role,
        description=spec.description,
        implemented=True,
        health=health,
        heartbeat_age_seconds=age,
        heartbeat_last_beat=float(heartbeat.last_beat),
        pid=heartbeat.pid,
        metadata=metadata,
        job_type=job_type,
    )


def _build_job_type_summaries(
    *,
    queue: JobQueue,
    now_ts: float,
) -> list[JobTypeSummary]:
    counts = queue.count_by_type_and_state()
    oldest = queue.oldest_queued_at_per_type()

    job_types_seen: set[str] = {jt for (jt, _state) in counts.keys()}
    # Always surface the canonical workers' job types so operators can
    # tell "executor wired + idle" from "executor missing". Without this
    # `runtime status` hides ``coding_execute`` (and the others) until
    # the first row lands, making the absence indistinguishable from a
    # mis-configured runtime.
    job_types_seen.update(_ALWAYS_VISIBLE_JOB_TYPES)
    summaries: list[JobTypeSummary] = []
    for job_type in sorted(job_types_seen):
        queued = counts.get((job_type, JobState.QUEUED.value), 0)
        in_progress = sum(
            counts.get((job_type, st.value), 0) for st in _IN_PROGRESS_STATES
        )
        saved = counts.get((job_type, JobState.SAVED.value), 0)
        failed_retryable = counts.get(
            (job_type, JobState.FAILED_RETRYABLE.value), 0
        )
        failed_terminal = counts.get(
            (job_type, JobState.FAILED_TERMINAL.value), 0
        )
        oldest_at = oldest.get(job_type)
        oldest_age = (
            max(0.0, now_ts - float(oldest_at)) if oldest_at is not None else None
        )
        summaries.append(
            JobTypeSummary(
                job_type=job_type,
                queued=queued,
                in_progress=in_progress,
                saved=saved,
                failed_retryable=failed_retryable,
                failed_terminal=failed_terminal,
                oldest_queued_age_seconds=oldest_age,
            )
        )
    return summaries


def _build_failed_recent(
    *,
    queue: JobQueue,
    limit: int,
    now_ts: float,
) -> list[FailedJobSummary]:
    failed: list[FailedJobSummary] = []
    for job in queue.recent_failed(limit=limit):
        error = _extract_error(job)
        failed.append(
            FailedJobSummary(
                job_id=job.job_id,
                job_type=job.job_type,
                role=job.role,
                state=job.state.value,
                attempt=job.attempt,
                age_seconds=max(0.0, now_ts - float(job.updated_at)),
                error=error,
            )
        )
    return failed


def _extract_error(job: Job) -> Optional[str]:
    """Pull the worker's one-line error string out of ``result_json``.

    Workers stamp ``result["error"]`` with a stable constant or the
    ``Type: short message`` shape from ``_short_error``. Detail
    fields (``detail``) follow but stay separate so the status
    renderer can keep the line short.
    """

    if not isinstance(job.result, Mapping):
        return None
    error = job.result.get("error")
    if not error:
        return None
    text = str(error).strip()
    return text or None


def _build_warnings(
    *,
    services: Sequence[ServiceStatus],
    job_types: Sequence[JobTypeSummary],
    failed_recent: Sequence[FailedJobSummary],
    autonomy_recent: Sequence[AutonomyTickSummary] = (),
    completion_funnel_recent: Sequence[CompletionFunnelSummary] = (),
) -> list[str]:
    """Surface conditions an operator should act on.

    Each warning is a single sentence and — where the next step is
    a concrete command — embeds it inline so the operator can copy
    it without leaving the status screen. M8 strengthening: STALE
    and UNKNOWN warnings now include the exact command(s) the
    operator should run to recover (`yule runtime up`,
    `yule run-service`, or systemd unit names).

    Round 4 of #73 added autonomy-driven warnings: an autonomy tick
    that errored, persistent ``locked_by_other`` rows (often a sign
    a producer crashed mid-tick), and recent completion-funnel rows
    parked on ``blocked`` / ``needs_approval``.
    """

    warnings: list[str] = []
    circuit_open = [s for s in services if s.health == HEALTH_CIRCUIT_OPEN]
    if circuit_open:
        warnings.append(
            "circuit open (supervisor stopped restarting): "
            + ", ".join(s.service_id for s in circuit_open)
            + " — run `yule runtime circuit reset <service_id>` to clear"
        )
    stale = [s for s in services if s.health == HEALTH_STALE]
    if stale:
        ids = ", ".join(s.service_id for s in stale)
        # Two recovery paths depending on how the operator is running
        # the runtime (single-host parent vs. systemd). Prefer naming
        # both so the hint is correct without asking the operator.
        first_id = stale[0].service_id
        warnings.append(
            f"stale heartbeat: {ids} — restart options: "
            f"`yule run-service {first_id}` (foreground/dev), "
            f"`systemctl restart yule-run-service@{first_id}.service` "
            "(systemd), or `yule runtime up` to respawn the whole "
            "engineering parent."
        )
    unknown_implemented = [
        s for s in services if s.health == HEALTH_UNKNOWN and s.implemented
    ]
    if unknown_implemented:
        ids = ", ".join(s.service_id for s in unknown_implemented)
        first_id = unknown_implemented[0].service_id
        # P0-T — restart-needed 와 token/env 문제를 분리해 안내.
        # graceful_disabled 는 아래 별 warning 으로 처리.
        warnings.append(
            f"no heartbeat (worker likely never started): {ids} — start "
            f"options: `yule runtime up` (single-host parent spawning all "
            f"workers) or `yule run-service {first_id}` (one worker "
            "foreground) or `systemctl start "
            f"yule-run-service@{first_id}.service` (systemd). Without "
            "one of these, the queue stays unpicked even though the "
            "gateway is enqueuing jobs."
        )

    # P0-T — graceful-disabled: token/env 설정 문제. restart 한다고 해결
    # 안 되니까 별 warning 으로 분리.
    disabled_services = [
        s
        for s in services
        if s.health == HEALTH_GRACEFUL_DISABLED and s.implemented
    ]
    if disabled_services:
        ids = ", ".join(s.service_id for s in disabled_services)
        env_keys = sorted(
            {
                str(s.metadata.get("env_key") or "")
                for s in disabled_services
                if isinstance(s.metadata, Mapping)
                and s.metadata.get("env_key")
            }
        )
        env_hint = f" (env: {', '.join(env_keys)})" if env_keys else ""
        warnings.append(
            f"graceful-disabled: {ids} — token/env 가 비어있어 자체적으로 "
            "꺼진 상태. restart 가 아니라 .env.local 의 토큰 채움이 "
            f"필요{env_hint}. 채운 뒤 `yule runtime up` 으로 다시 올리세요."
        )
    failed_terminal_total = sum(j.failed_terminal for j in job_types)
    if failed_terminal_total > 0:
        warnings.append(
            f"{failed_terminal_total} failed_terminal job(s) — "
            "operator review required (no automatic retry). Use "
            "`yule runtime status --json` or query the SQLite "
            "`job_queue` table for full result_json."
        )

    # Autonomy / completion-funnel warnings — surface what the runtime
    # has decided it cannot do without operator intervention.
    autonomy_errors = [
        t for t in autonomy_recent if t.error
    ]
    if autonomy_errors:
        ids = ", ".join(t.tick_id for t in autonomy_errors[:3])
        warnings.append(
            f"autonomy producer errored on {len(autonomy_errors)} recent "
            f"tick(s) ({ids}) — runtime may be falling behind. Inspect "
            "supervisor logs (`journalctl -u yule.target` or the "
            "`yule run-service eng-supervisor-watch` stdout) for the "
            "stack trace."
        )

    locked_dispatches: list[AutonomyDispatchSummary] = []
    for tick in autonomy_recent:
        for d in tick.dispatches:
            if d.outcome == AUTONOMY_OUTCOME_LOCKED:
                locked_dispatches.append(d)
    if locked_dispatches:
        sample = ", ".join(
            f"{d.source}:{d.session_id or d.executor_role or d.branch_hint}"
            for d in locked_dispatches[:3]
        )
        warnings.append(
            f"autonomy producer skipped {len(locked_dispatches)} "
            f"dispatch(es) due to held locks ({sample}) — usually transient, "
            "but if it persists across many ticks the lock holder may have "
            "crashed mid-tick. Restart the supervisor "
            "(`systemctl restart yule-run-service@eng-supervisor-watch.service`) "
            "to drop the in-memory registry."
        )

    blocked_funnel = [
        c for c in completion_funnel_recent if c.completion_status == "blocked"
    ]
    if blocked_funnel:
        ids = ", ".join(c.session_id or c.job_id for c in blocked_funnel[:3])
        warnings.append(
            f"{len(blocked_funnel)} session(s) parked on completion=blocked "
            f"({ids}) — runtime is NOT auto-advancing these. Inspect the "
            "session's last failure reason (look for `coding_execute_progress` "
            "in `session.extra`) and either requeue manually or close the "
            "session."
        )

    needs_approval = [
        c
        for c in completion_funnel_recent
        if c.completion_status == "needs_approval"
    ]
    if needs_approval:
        ids = ", ".join(c.session_id or c.job_id for c in needs_approval[:3])
        warnings.append(
            f"{len(needs_approval)} session(s) waiting on human approval "
            f"({ids}) — approval card lives in `#승인-대기`; reply "
            "`이대로 저장` (or the relevant approval action) to advance."
        )
    return warnings


# ---------------------------------------------------------------------------
# Re-exports — keep ``from .status import ...`` importers unchanged after
# the renderer / operator_actions / journal split. Placed AFTER all core
# definitions so the sibling modules' ``from .status import ...`` top-level
# imports resolve without an import-time cycle (the dataclasses + constants
# they need are already bound above by the time these lines execute).
# ---------------------------------------------------------------------------

from .status_journal import (  # noqa: E402 — re-export after core definitions
    RuntimeAutonomyJournal,
    _AUTONOMY_JOURNAL_MAX_ENTRIES,
    _DEFAULT_JOURNAL,
    _project_autonomy_tick,
    get_default_autonomy_journal,
    record_autonomy_report,
)
from .status_operator_actions import (  # noqa: E402 — re-export after core defs
    ACTION_KIND_AUTONOMY_ERROR,
    ACTION_KIND_BLOCKED,
    ACTION_KIND_CIRCUIT_OPEN,
    ACTION_KIND_FAILED_TERMINAL,
    ACTION_KIND_GRACEFUL_DISABLED,
    ACTION_KIND_LOCK_CONTENTION,
    ACTION_KIND_NEEDS_APPROVAL,
    ACTION_KIND_RETRY_READY_BACKLOG,
    ACTION_KIND_STALE_SERVICE,
    ACTION_KIND_UNKNOWN_SERVICE,
    OPERATOR_ACTION_HIGH,
    OPERATOR_ACTION_LOW,
    OPERATOR_ACTION_MEDIUM,
    CompactStatusSummary,
    OperatorAction,
    _SEVERITY_ORDER,
    build_compact_status_summary,
    summarize_operator_actions,
)
from .status_render import (  # noqa: E402 — re-export after core definitions
    render_autonomy_summary_markdown,
    render_live_smoke_checklist,
    render_runtime_status_compact,
    render_runtime_status_json,
    render_runtime_status_text,
)


__all__ = (
    "ACTION_KIND_AUTONOMY_ERROR",
    "ACTION_KIND_BLOCKED",
    "ACTION_KIND_CIRCUIT_OPEN",
    "ACTION_KIND_FAILED_TERMINAL",
    "ACTION_KIND_LOCK_CONTENTION",
    "ACTION_KIND_NEEDS_APPROVAL",
    "ACTION_KIND_RETRY_READY_BACKLOG",
    "ACTION_KIND_STALE_SERVICE",
    "ACTION_KIND_GRACEFUL_DISABLED",
    "ACTION_KIND_UNKNOWN_SERVICE",
    "AUTONOMY_OUTCOME_DEDUPED",
    "AUTONOMY_OUTCOME_DISPATCHED",
    "AUTONOMY_OUTCOME_ERROR",
    "AUTONOMY_OUTCOME_LOCKED",
    "AUTONOMY_OUTCOME_SKIPPED",
    "AutonomyDispatchSummary",
    "AutonomyTickSummary",
    "CompactStatusSummary",
    "CompletionFunnelSummary",
    "FailedJobSummary",
    "HEALTH_ALIVE",
    "HEALTH_CIRCUIT_OPEN",
    "HEALTH_GRACEFUL_DISABLED",
    "HEALTH_RESERVED",
    "HEALTH_STALE",
    "HEALTH_UNKNOWN",
    "JobTypeSummary",
    "OPERATOR_ACTION_HIGH",
    "OPERATOR_ACTION_LOW",
    "OPERATOR_ACTION_MEDIUM",
    "OperatorAction",
    "RuntimeAutonomyJournal",
    "RuntimeStatusReport",
    "ServiceStatus",
    "build_compact_status_summary",
    "build_runtime_status",
    "get_default_autonomy_journal",
    "record_autonomy_report",
    "render_autonomy_summary_markdown",
    "render_live_smoke_checklist",
    "render_runtime_status_compact",
    "render_runtime_status_json",
    "render_runtime_status_text",
    "summarize_operator_actions",
)
