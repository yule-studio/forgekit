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
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..agents.job_queue.heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    HeartbeatStore,
)
from ..agents.job_queue.state_machine import JobState
from ..agents.job_queue.store import Job, JobQueue
from .services import ServiceKind, ServiceSpec, list_services


# Health labels surfaced in renderer output. String constants so the
# ``--json`` consumer can match exact values without enum import.
HEALTH_ALIVE: str = "alive"
HEALTH_STALE: str = "stale"
HEALTH_UNKNOWN: str = "unknown"
HEALTH_RESERVED: str = "reserved"
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
    ServiceKind.SUPERVISOR: None,
    ServiceKind.DISCORD_GATEWAY: None,
    ServiceKind.RESERVED_DISCORD_GATEWAY: None,
}


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


# ---------------------------------------------------------------------------
# Operator action surface — Round 4 마무리.
# ---------------------------------------------------------------------------
#
# Round 4 후속까지는 "지금 큐가 어떤 상태인가" 까지만 surface 되어 있었다.
# 운영자가 그 상태에서 "그래서 내가 뭘 해야 하지?" 를 결정하려면 warnings
# 텍스트를 한 번 더 파싱해야 했다. 본 라운드는 그 결정을 코드 측에서 한 번
# 정렬해서, 텍스트/마크다운/JSON 모두 동일한 "operator action" 항목을 보고
# 동일한 다음 단계를 도출할 수 있게 만든다. 분류는 1:1 mapping:
#
#   * needs_approval (1+ session) → `#승인-대기` reply
#   * blocked (1+ session) → 사유 점검 + 수동 재진입/세션 종료 결정
#   * stalled_discussion → discussion follow-up worker 가 못 따라잡은 경우
#     (현재는 needs_approval 보다 약한 신호로 surface; producer 가 producer
#     tick 마다 재시도하므로 대기 가능)
#   * failed_ci → coding_execute 가 retry_ready 로 큐에 다시 들어간 경우
#     (CI orchestrator 가 잡고 있으나 30분 안에 동일 사유 반복 시 수동 점검)
#   * lock_contention → 같은 scope 의 lock 이 ticks 에 걸쳐 잡혀있는 경우
#   * stale_service / unknown_service / circuit_open / failed_terminal_jobs
#     → 기존 warning 라인의 운영자 명령을 OperatorAction 으로 정렬
#
# 모든 액션은 "high" / "medium" / "low" 세 단계만 사용한다 — Discord 포스트
# 헤더의 우선순위 정렬에 쓰이는 만큼 더 잘게 쪼개봐야 운영자 인지에 도움이
# 안 된다.


OPERATOR_ACTION_HIGH: str = "high"
OPERATOR_ACTION_MEDIUM: str = "medium"
OPERATOR_ACTION_LOW: str = "low"


# Stable kind keys — used by the JSON renderer + the markdown poster
# so a downstream consumer can route on action kind without parsing
# the headline string.
ACTION_KIND_NEEDS_APPROVAL: str = "needs_approval"
ACTION_KIND_BLOCKED: str = "blocked"
ACTION_KIND_RETRY_READY_BACKLOG: str = "retry_ready_backlog"
ACTION_KIND_LOCK_CONTENTION: str = "lock_contention"
ACTION_KIND_AUTONOMY_ERROR: str = "autonomy_error"
ACTION_KIND_STALE_SERVICE: str = "stale_service"
ACTION_KIND_UNKNOWN_SERVICE: str = "unknown_service"
ACTION_KIND_CIRCUIT_OPEN: str = "circuit_open"
ACTION_KIND_FAILED_TERMINAL: str = "failed_terminal_jobs"


@dataclass(frozen=True)
class OperatorAction:
    """One actionable item the operator should resolve.

    *kind* is a stable identifier (one of the ``ACTION_KIND_*``
    constants) so a Discord poster / dashboard can route on it
    without parsing the human-readable headline.

    *severity* is one of ``high`` / ``medium`` / ``low`` — the
    compact renderer sorts ``high`` first.

    *next_step* must be a copy-pasteable command or a literal
    Discord reply string when applicable; the operator should not
    need to read any other doc to act on a single action row.
    """

    kind: str
    severity: str
    headline: str
    next_step: str
    affected: Tuple[str, ...] = ()
    icon: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "headline": self.headline,
            "next_step": self.next_step,
            "affected": list(self.affected),
            "icon": self.icon,
        }


_SEVERITY_ORDER: Mapping[str, int] = {
    OPERATOR_ACTION_HIGH: 0,
    OPERATOR_ACTION_MEDIUM: 1,
    OPERATOR_ACTION_LOW: 2,
}


def summarize_operator_actions(
    report: "RuntimeStatusReport",
) -> Tuple[OperatorAction, ...]:
    """Project *report* into the actions an operator should resolve.

    Pure read — no side effects. Sorted ``high`` → ``low`` so a
    truncated render still surfaces the urgent items. Returns an
    empty tuple when nothing operator-actionable is going on (used
    by the compact view to render the green "all clear" line).
    """

    actions: list[OperatorAction] = []

    # --- circuit_open: supervisor stopped restarting on purpose.
    circuit_open = [
        s for s in report.services if s.health == HEALTH_CIRCUIT_OPEN
    ]
    if circuit_open:
        ids = tuple(s.service_id for s in circuit_open)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_CIRCUIT_OPEN,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"circuit OPEN — {len(ids)} service(s) won't auto-restart"
                ),
                next_step=f"yule runtime circuit reset {first}",
                affected=ids,
                icon="🛑",
            )
        )

    # --- stale services: was alive, went quiet.
    stale = [s for s in report.services if s.health == HEALTH_STALE]
    if stale:
        ids = tuple(s.service_id for s in stale)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_STALE_SERVICE,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} service(s) stale — heartbeat past deadline"
                ),
                next_step=(
                    f"yule run-service {first}  # or: systemctl restart "
                    f"yule-run-service@{first}.service"
                ),
                affected=ids,
                icon="💤",
            )
        )

    # --- unknown implemented services: never started.
    unknown = [
        s for s in report.services if s.health == HEALTH_UNKNOWN and s.implemented
    ]
    if unknown:
        ids = tuple(s.service_id for s in unknown)
        first = ids[0]
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_UNKNOWN_SERVICE,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} service(s) never reported a heartbeat"
                ),
                next_step=(
                    f"yule runtime up  # or single: yule run-service {first}"
                ),
                affected=ids,
                icon="❓",
            )
        )

    # --- failed_terminal jobs: no auto-retry.
    failed_terminal = [
        f for f in report.failed_recent if f.state == "failed_terminal"
    ]
    if failed_terminal:
        ids = tuple(f.job_id for f in failed_terminal)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_FAILED_TERMINAL,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} job(s) in failed_terminal — manual review only"
                ),
                next_step=(
                    "yule runtime status --json  # inspect result_json, then "
                    "requeue or close the session"
                ),
                affected=ids,
                icon="🧨",
            )
        )

    # --- needs_approval funnel rows — operator reply on `#승인-대기`.
    needs_approval = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "needs_approval"
    ]
    if needs_approval:
        ids = tuple(c.session_id or c.job_id for c in needs_approval)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_NEEDS_APPROVAL,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} session(s) waiting on `#승인-대기` reply"
                ),
                next_step=(
                    "Reply `이대로 저장` (또는 해당 카드의 승인 버튼) in "
                    "`#승인-대기` to advance"
                ),
                affected=ids,
                icon="🙋",
            )
        )

    # --- blocked funnel rows — manual decision required.
    blocked = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "blocked"
    ]
    if blocked:
        ids = tuple(c.session_id or c.job_id for c in blocked)
        # Surface up to two reasons inline so the operator can spot
        # whether everything blocked on the same root (e.g. all
        # `protected_branch_blocked`).
        reasons = sorted({c.reason for c in blocked if c.reason})[:2]
        why = f" — reason(s): {', '.join(reasons)}" if reasons else ""
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_BLOCKED,
                severity=OPERATOR_ACTION_HIGH,
                headline=(
                    f"{len(ids)} session(s) blocked — autonomy will not retry"
                    f"{why}"
                ),
                next_step=(
                    "Inspect `coding_execute_progress` in session.extra; "
                    "manually requeue or close the session."
                ),
                affected=ids,
                icon="⛔",
            )
        )

    # --- autonomy producer error — supervisor logs needed.
    errored_ticks = [t for t in report.autonomy_recent if t.error]
    if errored_ticks:
        ids = tuple(t.tick_id for t in errored_ticks)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_AUTONOMY_ERROR,
                severity=OPERATOR_ACTION_MEDIUM,
                headline=(
                    f"{len(ids)} autonomy tick(s) errored — runtime may be "
                    "falling behind"
                ),
                next_step=(
                    "journalctl -u yule.target  # search for "
                    "`autonomy producer` traceback near the tick id"
                ),
                affected=ids,
                icon="⚠️",
            )
        )

    # --- persistent lock contention — usually transient; medium severity.
    locked_dispatches: list[str] = []
    for tick in report.autonomy_recent:
        for d in tick.dispatches:
            if d.outcome == AUTONOMY_OUTCOME_LOCKED:
                locked_dispatches.append(
                    d.session_id or d.executor_role or d.branch_hint or d.source
                )
    if locked_dispatches:
        unique_ids = tuple(sorted(set(locked_dispatches)))
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_LOCK_CONTENTION,
                severity=OPERATOR_ACTION_MEDIUM,
                headline=(
                    f"{len(locked_dispatches)} dispatch(es) blocked on locks "
                    f"({len(unique_ids)} scope(s))"
                ),
                next_step=(
                    "Usually clears within 1-2 ticks. If it persists, "
                    "`systemctl restart "
                    "yule-run-service@eng-supervisor-watch.service` to drop "
                    "the in-memory lock registry."
                ),
                affected=unique_ids,
                icon="🔒",
            )
        )

    # --- retry_ready backlog (low severity — informational).
    # A handful is fine, but a session that keeps landing on
    # retry_ready hints at a CI loop the operator may want to look at.
    retry_ready = [
        c
        for c in report.completion_funnel_recent
        if c.completion_status == "retry_ready"
    ]
    if len(retry_ready) >= 3:
        ids = tuple(c.session_id or c.job_id for c in retry_ready)
        actions.append(
            OperatorAction(
                kind=ACTION_KIND_RETRY_READY_BACKLOG,
                severity=OPERATOR_ACTION_LOW,
                headline=(
                    f"{len(ids)} retry_ready completions in recent funnel — "
                    "CI may be flapping"
                ),
                next_step=(
                    "Inspect the failing PR (gh pr checks <pr>) and decide "
                    "whether to keep auto-retrying or close the session."
                ),
                affected=ids,
                icon="🔁",
            )
        )

    actions.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))
    return tuple(actions)


# ---------------------------------------------------------------------------
# Compact summary — short helper for journal logs / Discord top-line.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactStatusSummary:
    """One-line counters + the top operator action.

    The full :class:`RuntimeStatusReport` stays the source of truth;
    this projection is the "what would I tweet about the runtime
    right now" view. Used by the supervisor's compact log line and
    by ``yule runtime status --compact`` (future CLI).
    """

    profile: str
    services_alive: int
    services_stale: int
    services_unknown: int
    services_circuit_open: int
    queue_in_progress: int
    queue_failed_terminal: int
    queue_failed_retryable: int
    autonomy_ticks_recent: int
    autonomy_ticks_errored: int
    autonomy_locked_dispatches: int
    funnel_done: int
    funnel_retry_ready: int
    funnel_needs_approval: int
    funnel_blocked: int
    top_action: Optional[OperatorAction]
    actions_total: int

    def is_clean(self) -> bool:
        """True when there is nothing operator-actionable to do."""

        return self.top_action is None and self.actions_total == 0


def build_compact_status_summary(
    report: "RuntimeStatusReport",
    *,
    actions: Optional[Sequence[OperatorAction]] = None,
) -> CompactStatusSummary:
    """Project *report* into a :class:`CompactStatusSummary`.

    *actions* defaults to the result of
    :func:`summarize_operator_actions(report)` so the caller can
    request both views with one pass when they need both. Tests pass
    a precomputed tuple to verify the projection is independent of
    the mapping function.
    """

    action_seq = (
        tuple(actions) if actions is not None else summarize_operator_actions(report)
    )

    services_alive = sum(1 for s in report.services if s.health == HEALTH_ALIVE)
    services_stale = sum(1 for s in report.services if s.health == HEALTH_STALE)
    services_unknown = sum(
        1 for s in report.services if s.health == HEALTH_UNKNOWN and s.implemented
    )
    services_circuit_open = sum(
        1 for s in report.services if s.health == HEALTH_CIRCUIT_OPEN
    )

    queue_in_progress = sum(j.in_progress for j in report.job_types)
    queue_failed_terminal = sum(j.failed_terminal for j in report.job_types)
    queue_failed_retryable = sum(j.failed_retryable for j in report.job_types)

    autonomy_ticks_errored = sum(1 for t in report.autonomy_recent if t.error)
    autonomy_locked_dispatches = sum(
        1
        for t in report.autonomy_recent
        for d in t.dispatches
        if d.outcome == AUTONOMY_OUTCOME_LOCKED
    )

    funnel_done = sum(
        1 for c in report.completion_funnel_recent if c.completion_status == "done"
    )
    funnel_retry_ready = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "retry_ready"
    )
    funnel_needs_approval = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "needs_approval"
    )
    funnel_blocked = sum(
        1
        for c in report.completion_funnel_recent
        if c.completion_status == "blocked"
    )

    top = action_seq[0] if action_seq else None
    return CompactStatusSummary(
        profile=report.profile,
        services_alive=services_alive,
        services_stale=services_stale,
        services_unknown=services_unknown,
        services_circuit_open=services_circuit_open,
        queue_in_progress=queue_in_progress,
        queue_failed_terminal=queue_failed_terminal,
        queue_failed_retryable=queue_failed_retryable,
        autonomy_ticks_recent=len(report.autonomy_recent),
        autonomy_ticks_errored=autonomy_ticks_errored,
        autonomy_locked_dispatches=autonomy_locked_dispatches,
        funnel_done=funnel_done,
        funnel_retry_ready=funnel_retry_ready,
        funnel_needs_approval=funnel_needs_approval,
        funnel_blocked=funnel_blocked,
        top_action=top,
        actions_total=len(action_seq),
    )


def render_runtime_status_compact(
    report: "RuntimeStatusReport",
    *,
    actions: Optional[Sequence[OperatorAction]] = None,
) -> str:
    """Return the ≤6-line compact digest for ``yule runtime status --compact``.

    Designed to be safe to log every supervisor watch tick — short,
    no Discord-only markdown, copy-pasteable into Slack/journalctl
    without further shaping.
    """

    summary = build_compact_status_summary(report, actions=actions)
    lines: list[str] = []
    lines.append(f"🛰 runtime[{summary.profile}] @ {_fmt_unix(report.generated_at)}")
    lines.append(
        f"  services: {summary.services_alive} alive · "
        f"{summary.services_stale} stale · "
        f"{summary.services_unknown} unknown · "
        f"{summary.services_circuit_open} circuit_open"
    )
    lines.append(
        f"  queue: {summary.queue_in_progress} in_progress · "
        f"{summary.queue_failed_retryable} failed_retryable · "
        f"{summary.queue_failed_terminal} failed_terminal"
    )
    lines.append(
        f"  autonomy: {summary.autonomy_ticks_recent} ticks · "
        f"{summary.autonomy_ticks_errored} errored · "
        f"{summary.autonomy_locked_dispatches} locked"
    )
    lines.append(
        f"  funnel: {summary.funnel_done} done · "
        f"{summary.funnel_retry_ready} retry_ready · "
        f"{summary.funnel_needs_approval} needs_approval · "
        f"{summary.funnel_blocked} blocked"
    )
    if summary.top_action is None:
        lines.append("  next: ✅ no operator action required")
    else:
        action = summary.top_action
        more = (
            f" (+{summary.actions_total - 1} more)"
            if summary.actions_total > 1
            else ""
        )
        lines.append(
            f"  next: {action.icon or '!'} [{action.severity}] "
            f"{action.headline} → {action.next_step}{more}"
        )
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# RuntimeAutonomyJournal — process-local ring buffer the supervisor
# populates after every autonomy producer tick. The status builder
# reads it back when rendering the report.
# ---------------------------------------------------------------------------


# Default ring depth — small on purpose: the operator surface only
# needs "what happened in the last few ticks", not a full audit log
# (the agent_ops audit log + SQLite job_queue rows are the system of
# record). 16 entries comfortably covers a few minutes of producer
# activity at the default 30 s tick interval.
_AUTONOMY_JOURNAL_MAX_ENTRIES: int = 16


class RuntimeAutonomyJournal:
    """In-memory ring buffer of recent autonomy producer ticks.

    Process-local, thread-safe (the supervisor's autonomy hook may
    fire from a different task than the status post hook). Designed
    so callers can record full :class:`AutonomyProducerReport`-like
    objects and the journal projects each into a status-friendly
    :class:`AutonomyTickSummary`.

    The journal does NOT persist — supervisor restarts naturally
    drop the in-memory ticks. The agent_ops audit log on
    ``session.extra`` is the durable record; this layer only
    answers "what is the runtime doing right now?".
    """

    def __init__(self, *, max_entries: int = _AUTONOMY_JOURNAL_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._max = max(1, int(max_entries))
        self._entries: Deque[AutonomyTickSummary] = deque(maxlen=self._max)
        self._locks_held: Tuple[str, ...] = ()

    @property
    def max_entries(self) -> int:
        return self._max

    def record_report(self, report: Any) -> Optional[AutonomyTickSummary]:
        """Project *report* into a summary and append to the journal.

        Returns the projection (or ``None`` if *report* couldn't be
        projected — e.g. a stub object missing the expected fields).
        Never raises: the caller is the supervisor's last-resort hook
        and a bad projection must not derail the loop.
        """

        try:
            summary = _project_autonomy_tick(report)
        except Exception:  # noqa: BLE001 - never crash supervisor on a bad shape
            return None
        if summary is None:
            return None
        with self._lock:
            self._entries.append(summary)
            self._locks_held = tuple(summary.locks_held)
        return summary

    def recent(self, *, limit: Optional[int] = None) -> Tuple[AutonomyTickSummary, ...]:
        """Return up to *limit* ticks, newest first."""

        with self._lock:
            entries = list(self._entries)
        entries.reverse()
        if limit is not None and limit >= 0:
            entries = entries[: int(limit)]
        return tuple(entries)

    def locks_held(self) -> Tuple[str, ...]:
        """Snapshot of locks held at the most recent tick."""

        with self._lock:
            return tuple(self._locks_held)

    def reset(self) -> None:
        """Drop every recorded tick. Used by tests."""

        with self._lock:
            self._entries.clear()
            self._locks_held = ()


def _project_autonomy_tick(report: Any) -> Optional[AutonomyTickSummary]:
    """Liberal projection from any "report-like" object → summary.

    Accepts both the real :class:`AutonomyProducerReport` and any
    duck-typed object exposing the same field names (used by tests).
    """

    if report is None:
        return None
    tick_id = str(getattr(report, "tick_id", "") or "")
    started = str(getattr(report, "started_at", "") or "")
    finished = str(getattr(report, "finished_at", "") or "")
    candidate = getattr(report, "next_task_candidate", None)
    next_source: Optional[str] = None
    if candidate is not None:
        raw = getattr(candidate, "source", None)
        if raw:
            next_source = str(raw)
    summary_fn = getattr(report, "summary_line", None)
    if callable(summary_fn):
        try:
            summary_line = str(summary_fn() or "")
        except Exception:  # noqa: BLE001
            summary_line = ""
    else:
        summary_line = ""
    dispatches_raw = getattr(report, "dispatches", ()) or ()
    dispatches: List[AutonomyDispatchSummary] = []
    for entry in dispatches_raw:
        try:
            dispatches.append(
                AutonomyDispatchSummary(
                    source=str(getattr(entry, "source", "") or ""),
                    outcome=str(getattr(entry, "outcome", "") or ""),
                    session_id=str(getattr(entry, "session_id", "") or ""),
                    executor_role=str(getattr(entry, "executor_role", "") or ""),
                    job_id=getattr(entry, "job_id", None),
                    branch_hint=str(getattr(entry, "branch_hint", "") or ""),
                    reason=str(getattr(entry, "reason", "") or ""),
                )
            )
        except Exception:  # noqa: BLE001 - skip malformed dispatches
            continue
    locks_held_raw = getattr(report, "locks_held", ()) or ()
    locks_held = tuple(str(s) for s in locks_held_raw)
    error = getattr(report, "error", None)
    return AutonomyTickSummary(
        tick_id=tick_id,
        started_at=started,
        finished_at=finished,
        next_task_source=next_source,
        summary_line=summary_line,
        dispatches=tuple(dispatches),
        locks_held=locks_held,
        error=str(error) if error else None,
    )


# Module-level default journal — the supervisor's autonomy hook
# writes to it, and :func:`build_runtime_status` reads from it. Tests
# can pass an explicit instance to keep state isolated.
_DEFAULT_JOURNAL: RuntimeAutonomyJournal = RuntimeAutonomyJournal()


def get_default_autonomy_journal() -> RuntimeAutonomyJournal:
    """Return the process-local default journal singleton."""

    return _DEFAULT_JOURNAL


def record_autonomy_report(report: Any) -> Optional[AutonomyTickSummary]:
    """Record *report* into the default journal — supervisor hook.

    Returns the projected summary (or ``None`` if the projection
    failed). Never raises — calling this from the supervisor's
    on-report callback must be safe regardless of report shape.
    """

    return _DEFAULT_JOURNAL.record_report(report)


def build_runtime_status(
    *,
    profile: str = "engineering",
    queue: JobQueue,
    heartbeats: HeartbeatStore,
    deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    failed_limit: int = 10,
    now: Optional[float] = None,
    circuit_snapshots: Optional[Mapping[str, Any]] = None,
    autonomy_journal: Optional[RuntimeAutonomyJournal] = None,
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
    health = HEALTH_ALIVE if age <= max(1.0, float(deadline_seconds)) else HEALTH_STALE
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
        metadata=dict(heartbeat.metadata or {}),
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
        warnings.append(
            f"no heartbeat (worker likely never started): {ids} — start "
            f"options: `yule runtime up` (single-host parent spawning all "
            f"workers) or `yule run-service {first_id}` (one worker "
            "foreground) or `systemctl start "
            f"yule-run-service@{first_id}.service` (systemd). Without "
            "one of these, the queue stays unpicked even though the "
            "gateway is enqueuing jobs."
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
# Live smoke checklist
# ---------------------------------------------------------------------------
#
# A short, deterministic "what to check next" block appended to the
# text render. Operators see the same screen they used to derive
# health from, plus the exact commands they should run to confirm a
# real Discord smoke pass (see docs/discord.md §10).


_LIVE_SMOKE_CHECKLIST: Tuple[str, ...] = (
    "1. `yule runtime up --dry-run` — confirm 12 services planned "
    "(1 supervisor + 1 research + 7 role + approval + obsidian + "
    "gateway).",
    "2. `yule runtime up` (this terminal) or `systemctl start "
    "yule.target` (systemd) — start the runtime parent / units.",
    "3. `yule runtime status --profile engineering` — every service "
    "should be ALIVE; STALE/UNKNOWN warnings list the exact restart "
    "command.",
    "4. `#업무-접수` test message → eng-discord-gateway enqueues "
    "research_collect → eng-research-worker pulls it → role workers "
    "produce takes (queue counts move through queued→saved).",
    "5. Reply `이대로 저장` in `#승인-대기` → eng-approval-worker "
    "ingests reply → eng-obsidian-writer writes vault note. Verify "
    "with `yule runtime status` (obsidian_write saved += 1) + the "
    "new file under OBSIDIAN_VAULT_PATH.",
    "6. Trip a worker on purpose (kill `eng-role-tech-lead`) → status "
    "must show STALE → restart hint above must list the exact unit "
    "id. Failure here means the operator hint regressed.",
)


def render_live_smoke_checklist(
    report: Optional[RuntimeStatusReport] = None,
) -> str:
    """Return the live-smoke checklist as a numbered text block.

    *report* is accepted but currently unused — passing it lets a
    future revision tailor lines to the actual deployment (e.g. omit
    the `systemctl` line on macOS dev hosts). Today the block is
    deployment-agnostic so the operator gets the same checklist
    whichever environment they run from.
    """

    lines = ["live smoke checklist:"]
    for item in _LIVE_SMOKE_CHECKLIST:
        lines.append(f"  {item}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_runtime_status_text(report: RuntimeStatusReport) -> str:
    """Human-readable single-screen render.

    Section order:

    1. ``profile`` / ``generated_at`` header.
    2. ``services`` — one line per service: health, id, role, queue
       job_type, heartbeat age, pid + a short description sub-line so
       the operator sees what the service actually does.
    3. ``queue`` — per-job-type summary.
    4. ``recent failures`` — most recent first.
    5. ``warnings`` — actionable next-step (with exact commands for
       STALE/UNKNOWN/circuit-open).
    6. ``live smoke checklist`` — deterministic 6-step verification
       block so the operator can copy commands from one screen.
    """

    lines: list[str] = []
    lines.append(f"profile: {report.profile}")
    lines.append(
        f"generated_at: {_fmt_unix(report.generated_at)} "
        f"(heartbeat deadline: {_fmt_seconds(report.deadline_seconds)})"
    )
    lines.append("")

    lines.append("services:")
    if not report.services:
        lines.append("  (none)")
    else:
        for svc in report.services:
            lines.append("  " + _format_service_line(svc))
            if svc.description:
                lines.append("    handles: " + svc.description)
    lines.append("")

    lines.append("queue:")
    if not report.job_types:
        lines.append("  (no jobs in queue)")
    else:
        for jt in report.job_types:
            lines.append("  " + _format_job_type_line(jt))
    lines.append("")

    lines.append("recent failures:")
    if not report.failed_recent:
        lines.append("  (none)")
    else:
        for fj in report.failed_recent:
            lines.append("  " + _format_failed_line(fj))

    lines.append("")
    lines.append("autonomy producer:")
    if not report.autonomy_recent:
        lines.append("  (no recent ticks recorded)")
    else:
        for tick in report.autonomy_recent:
            lines.append("  " + _format_autonomy_tick_line(tick))
            for d in tick.dispatches:
                lines.append("    " + _format_dispatch_line(d))
    if report.autonomy_locks_held:
        lines.append(
            "  locks held: " + ", ".join(report.autonomy_locks_held)
        )

    lines.append("")
    lines.append("completion funnel:")
    if not report.completion_funnel_recent:
        lines.append("  (no recent completions)")
    else:
        for c in report.completion_funnel_recent:
            lines.append("  " + _format_funnel_line(c))

    actions = summarize_operator_actions(report)
    lines.append("")
    lines.append("operator actions:")
    if not actions:
        lines.append("  ✅ no operator action required")
    else:
        for action in actions:
            lines.append("  " + _format_operator_action_line(action))

    if report.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in report.warnings:
            lines.append(f"  ! {warning}")

    lines.append("")
    lines.append(render_live_smoke_checklist(report))

    return "\n".join(lines)


def _format_operator_action_line(action: OperatorAction) -> str:
    icon = action.icon or "!"
    affected = ""
    if action.affected:
        head = ", ".join(action.affected[:3])
        if len(action.affected) > 3:
            head += f" (+{len(action.affected) - 3} more)"
        affected = f" affected={head}"
    return (
        f"{icon} [{action.severity}] {action.kind} — {action.headline}"
        f"{affected}\n      → {action.next_step}"
    )


def render_runtime_status_json(report: RuntimeStatusReport) -> str:
    """Stable JSON render for ``--json``.

    Keys mirror the dataclass field names so a downstream consumer
    can parse with minimal mapping. ``ensure_ascii=False`` so
    Korean role labels survive the round trip when redirected to a
    file.
    """

    payload = {
        "profile": report.profile,
        "generated_at": report.generated_at,
        "deadline_seconds": report.deadline_seconds,
        "services": [
            {
                "service_id": s.service_id,
                "kind": s.kind,
                "role": s.role,
                "description": s.description,
                "implemented": s.implemented,
                "health": s.health,
                "heartbeat_age_seconds": s.heartbeat_age_seconds,
                "heartbeat_last_beat": s.heartbeat_last_beat,
                "pid": s.pid,
                "metadata": dict(s.metadata),
                "job_type": s.job_type,
            }
            for s in report.services
        ],
        "job_types": [
            {
                "job_type": j.job_type,
                "queued": j.queued,
                "in_progress": j.in_progress,
                "saved": j.saved,
                "failed_retryable": j.failed_retryable,
                "failed_terminal": j.failed_terminal,
                "oldest_queued_age_seconds": j.oldest_queued_age_seconds,
            }
            for j in report.job_types
        ],
        "failed_recent": [
            {
                "job_id": f.job_id,
                "job_type": f.job_type,
                "role": f.role,
                "state": f.state,
                "attempt": f.attempt,
                "age_seconds": f.age_seconds,
                "error": f.error,
            }
            for f in report.failed_recent
        ],
        "autonomy_recent": [
            {
                "tick_id": t.tick_id,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "next_task_source": t.next_task_source,
                "summary_line": t.summary_line,
                "error": t.error,
                "dispatches": [
                    {
                        "source": d.source,
                        "outcome": d.outcome,
                        "session_id": d.session_id,
                        "executor_role": d.executor_role,
                        "job_id": d.job_id,
                        "branch_hint": d.branch_hint,
                        "reason": d.reason,
                    }
                    for d in t.dispatches
                ],
                "locks_held": list(t.locks_held),
            }
            for t in report.autonomy_recent
        ],
        "completion_funnel_recent": [
            {
                "session_id": c.session_id,
                "job_id": c.job_id,
                "job_type": c.job_type,
                "completion_status": c.completion_status,
                "ticked": c.ticked,
                "reason": c.reason,
                "recommended_source": c.recommended_source,
                "producer_summary": c.producer_summary,
                "at": c.at,
            }
            for c in report.completion_funnel_recent
        ],
        "autonomy_locks_held": list(report.autonomy_locks_held),
        "warnings": list(report.warnings),
        "operator_actions": [a.to_payload() for a in summarize_operator_actions(report)],
        "compact": _compact_summary_payload(build_compact_status_summary(report)),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _compact_summary_payload(summary: CompactStatusSummary) -> Mapping[str, Any]:
    return {
        "profile": summary.profile,
        "services_alive": summary.services_alive,
        "services_stale": summary.services_stale,
        "services_unknown": summary.services_unknown,
        "services_circuit_open": summary.services_circuit_open,
        "queue_in_progress": summary.queue_in_progress,
        "queue_failed_terminal": summary.queue_failed_terminal,
        "queue_failed_retryable": summary.queue_failed_retryable,
        "autonomy_ticks_recent": summary.autonomy_ticks_recent,
        "autonomy_ticks_errored": summary.autonomy_ticks_errored,
        "autonomy_locked_dispatches": summary.autonomy_locked_dispatches,
        "funnel_done": summary.funnel_done,
        "funnel_retry_ready": summary.funnel_retry_ready,
        "funnel_needs_approval": summary.funnel_needs_approval,
        "funnel_blocked": summary.funnel_blocked,
        "actions_total": summary.actions_total,
        "top_action": (
            summary.top_action.to_payload() if summary.top_action else None
        ),
        "is_clean": summary.is_clean(),
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_service_line(svc: ServiceStatus) -> str:
    # circuit_open is one char wider than the other labels; widen the
    # column so columns stay aligned without truncating.
    health = svc.health.upper().ljust(13)
    role_part = f" role={svc.role}" if svc.role else ""
    pid_part = f" pid={svc.pid}" if svc.pid is not None else ""
    age_part = (
        f" beat={_fmt_seconds(svc.heartbeat_age_seconds)} ago"
        if svc.heartbeat_age_seconds is not None
        else " beat=—"
    )
    job_part = f" jt={svc.job_type}" if svc.job_type else ""
    return f"{health} {svc.service_id}{role_part}{job_part}{age_part}{pid_part}"


def _format_job_type_line(jt: JobTypeSummary) -> str:
    oldest = (
        f" oldest_queued={_fmt_seconds(jt.oldest_queued_age_seconds)} ago"
        if jt.oldest_queued_age_seconds is not None
        else ""
    )
    return (
        f"{jt.job_type:<22} queued={jt.queued} "
        f"in_progress={jt.in_progress} saved={jt.saved} "
        f"failed_retryable={jt.failed_retryable} "
        f"failed_terminal={jt.failed_terminal}{oldest}"
    )


def _format_failed_line(fj: FailedJobSummary) -> str:
    role_part = f" role={fj.role}" if fj.role else ""
    error_part = f" — {fj.error}" if fj.error else ""
    return (
        f"[{fj.state}] {fj.job_id} "
        f"job_type={fj.job_type}{role_part} attempt={fj.attempt} "
        f"age={_fmt_seconds(fj.age_seconds)}{error_part}"
    )


def _format_autonomy_tick_line(tick: AutonomyTickSummary) -> str:
    error_part = f" ERROR={tick.error}" if tick.error else ""
    next_part = f" next={tick.next_task_source}" if tick.next_task_source else ""
    summary = f" — {tick.summary_line}" if tick.summary_line else ""
    return f"[{tick.tick_id}]{next_part}{error_part}{summary}"


def _format_dispatch_line(d: AutonomyDispatchSummary) -> str:
    parts: list[str] = [f"{d.source}={d.outcome}"]
    if d.executor_role:
        parts.append(f"role={d.executor_role}")
    if d.session_id:
        parts.append(f"session={d.session_id}")
    if d.job_id:
        parts.append(f"job={d.job_id}")
    if d.branch_hint:
        parts.append(f"branch={d.branch_hint}")
    if d.reason:
        parts.append(f"why={d.reason}")
    return " ".join(parts)


def _format_funnel_line(c: CompletionFunnelSummary) -> str:
    tick_part = "ticked" if c.ticked else "no_tick"
    rec_part = (
        f" rec={c.recommended_source}" if c.recommended_source else ""
    )
    reason_part = f" — {c.reason}" if c.reason else ""
    return (
        f"[{c.completion_status}] session={c.session_id or '—'} "
        f"job={c.job_id} job_type={c.job_type} {tick_part}{rec_part}"
        f"{reason_part}"
    )


# ---------------------------------------------------------------------------
# Markdown renderer for the autonomy / funnel sections of the
# ``#봇-상태`` post. The base ``runtime.status_summary`` module owns
# the heartbeat / circuit / failed_terminal / fallback sections; this
# helper appends Round 4's autonomy + funnel rows so the operator
# sees what the runtime decided to do next inside the same post.
# ---------------------------------------------------------------------------


_AUTONOMY_OUTCOME_ICON: Mapping[str, str] = {
    AUTONOMY_OUTCOME_DISPATCHED: "✅",
    AUTONOMY_OUTCOME_DEDUPED: "♻️",
    AUTONOMY_OUTCOME_LOCKED: "🔒",
    AUTONOMY_OUTCOME_SKIPPED: "⏭",
    AUTONOMY_OUTCOME_ERROR: "⚠️",
}


_FUNNEL_STATUS_ICON: Mapping[str, str] = {
    "done": "✅",
    "retry_ready": "🔁",
    "needs_approval": "🙋",
    "blocked": "⛔",
}


_FUNNEL_STATUS_HINT: Mapping[str, str] = {
    "done": "completed → producer ticked",
    "retry_ready": "transient failure → producer ticked (retry path)",
    "needs_approval": "waiting on `#승인-대기` reply",
    "blocked": "blocked — operator review required",
}


def render_autonomy_summary_markdown(
    report: RuntimeStatusReport,
    *,
    max_ticks: int = 3,
    max_funnel: int = 5,
    max_actions: int = 5,
) -> str:
    """Return the autonomy / funnel markdown sections for ``#봇-상태``.

    Returns the empty string when nothing operator-actionable is
    showing — the caller (status_poster) appends the result to the
    base markdown output, so an "all clear" snapshot doesn't grow
    the post.

    The Round 4 마무리 layout puts the operator-action checklist at
    the top so the most urgent next-step is visible above the fold,
    then producer ticks, then funnel rows. Each section renders
    independently and is omitted when empty.
    """

    sections: list[str] = []

    actions_section = _render_operator_actions_section(
        summarize_operator_actions(report)[:max_actions]
    )
    if actions_section:
        sections.append(actions_section)

    autonomy_section = _render_autonomy_section(
        report.autonomy_recent[:max_ticks],
        locks_held=report.autonomy_locks_held,
    )
    if autonomy_section:
        sections.append(autonomy_section)

    funnel_section = _render_funnel_section(
        report.completion_funnel_recent[:max_funnel]
    )
    if funnel_section:
        sections.append(funnel_section)

    if not sections:
        return ""
    return "\n\n".join(sections)


def _render_operator_actions_section(
    actions: Sequence[OperatorAction],
) -> Optional[str]:
    """Render the "what should the operator do next" markdown block.

    Returns ``None`` when *actions* is empty so the parent renderer
    can skip the section and keep the post compact when the runtime
    is healthy.
    """

    if not actions:
        return None
    lines = ["### Operator actions"]
    for action in actions:
        icon = action.icon or "•"
        affected_part = ""
        if action.affected:
            head = ", ".join(f"`{a}`" for a in action.affected[:3])
            if len(action.affected) > 3:
                head += f" (+{len(action.affected) - 3} more)"
            affected_part = f" · affected: {head}"
        lines.append(
            f"- {icon} **[{action.severity}] {action.headline}**{affected_part}"
        )
        lines.append(f"  · 다음 단계: `{action.next_step}`")
    return "\n".join(lines)


def _render_autonomy_section(
    ticks: Sequence[AutonomyTickSummary],
    *,
    locks_held: Sequence[str],
) -> Optional[str]:
    """Render the autonomy producer section, or ``None`` if quiet.

    Quiet = no ticks recorded at all. We DO render when the most recent
    tick was idle so an operator can confirm "the producer ran and
    found nothing", which is different from "the producer never ran".
    """

    if not ticks and not locks_held:
        return None
    lines = ["### Autonomy producer"]
    if not ticks:
        lines.append("- _no ticks recorded yet_")
    else:
        for tick in ticks:
            head = _format_tick_markdown_head(tick)
            lines.append(head)
            for dispatch in tick.dispatches:
                lines.append(
                    "  - " + _format_dispatch_markdown(dispatch)
                )
            if tick.error:
                lines.append(f"  - ⚠️ tick error: `{tick.error}`")
    if locks_held:
        joined = ", ".join(f"`{s}`" for s in locks_held)
        lines.append(f"- 🔒 locks held: {joined}")
    return "\n".join(lines)


def _format_tick_markdown_head(tick: AutonomyTickSummary) -> str:
    next_part = (
        f" next=`{tick.next_task_source}`"
        if tick.next_task_source
        else ""
    )
    summary = f" — {tick.summary_line}" if tick.summary_line else ""
    icon = "⚠️" if tick.error else "🛞"
    return f"- {icon} `{tick.tick_id}`{next_part}{summary}"


def _format_dispatch_markdown(d: AutonomyDispatchSummary) -> str:
    icon = _AUTONOMY_OUTCOME_ICON.get(d.outcome, "•")
    parts: list[str] = [f"{icon} `{d.source}` → **{d.outcome}**"]
    if d.executor_role:
        parts.append(f"role=`{d.executor_role}`")
    if d.session_id:
        parts.append(f"session=`{d.session_id}`")
    if d.job_id:
        parts.append(f"job=`{d.job_id}`")
    if d.branch_hint:
        parts.append(f"branch=`{d.branch_hint}`")
    line = " · ".join(parts)
    if d.reason:
        line += f" — {d.reason}"
    return line


def _render_funnel_section(
    funnel: Sequence[CompletionFunnelSummary],
) -> Optional[str]:
    if not funnel:
        return None
    lines = ["### Completion funnel"]
    for c in funnel:
        icon = _FUNNEL_STATUS_ICON.get(c.completion_status, "•")
        hint = _FUNNEL_STATUS_HINT.get(
            c.completion_status, c.reason or "(no reason)"
        )
        rec_part = (
            f" → producer source `{c.recommended_source}`"
            if c.recommended_source
            else ""
        )
        ticked_part = " (ticked)" if c.ticked else ""
        sess = c.session_id or "—"
        lines.append(
            f"- {icon} **{c.completion_status}** session=`{sess}` "
            f"job_type=`{c.job_type}`{rec_part}{ticked_part} — {hint}"
        )
        if c.reason and c.reason != hint:
            lines.append(f"  · 사유: {c.reason}")
    return "\n".join(lines)


def _fmt_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    seconds = float(value)
    if seconds < 1.0:
        return f"{seconds:.2f}s"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60.0:
        return f"{minutes:.1f}m"
    hours = minutes / 60.0
    if hours < 24.0:
        return f"{hours:.1f}h"
    days = hours / 24.0
    return f"{days:.1f}d"


def _fmt_unix(value: float) -> str:
    """ISO-ish UTC for stable rendering across operator machines."""

    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(float(value), tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
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
