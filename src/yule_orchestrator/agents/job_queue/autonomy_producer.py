"""Autonomy producer / scheduler — Round 4 of #73.

Round 3 landed the *consumers* (coding_execute_dispatcher → executor
worker → CI retry orchestrator → completion hook). What was missing
was the **producer that runs without a human prompt**: a periodic
tick that scans the world, deduplicates against in-flight work, and
enqueues the next job so the runtime keeps moving forward after every
completion.

This module is that scheduler. It is deliberately small — most of the
hard logic already lives in the existing modules:

  * :mod:`coding_execute_dispatcher` knows how to lift approved coding
    jobs into ``coding_execute`` rows.
  * :mod:`next_task_selector` knows the priority ranking when several
    candidates are eligible.
  * :mod:`ci_retry_orchestrator` knows how to react when a PR's CI
    finishes.
  * :mod:`autonomy_lock` keeps two concurrent ticks from racing on the
    same branch / session.

The producer ties them together. One :meth:`AutonomyProducer.tick`
call is one full scheduling pass: each enabled sub-producer fires in
priority order, each respects the registry's locks, and each writes
an idempotency marker on the originating session so the *next* tick
doesn't re-fire on the same row.

What is **not** here, by design:

  * Direct queue writes outside of the existing producer modules. The
    autonomy producer never talks to ``JobQueue.enqueue`` directly —
    it goes through ``CodingExecutorWorker.enqueue``,
    ``RoleTakeWorker.enqueue``, etc., so dedup stays in one place.
  * Live LLM decision making. Sub-producers call into a
    :class:`ClaudeDecisionPort` seam (see
    ``claude_decision_seam.py``) when ambiguity needs to be resolved;
    the live provider is wired in a follow-up PR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .autonomy_lock import (
    AutonomyLock,
    AutonomyLockRegistry,
    branch_scope,
    coding_job_scope,
    session_scope,
)
from .coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    DispatchedCodingJob,
    WorkflowSessionState,
    dispatch_ready_coding_jobs,
    iter_ready_coding_jobs,
)
from .coding_executor_worker import CodingExecutorWorker
from .next_task_selector import (
    NextTaskCandidate,
    SOURCE_APPROVED_CODING_JOB,
    SOURCE_CI_FAILED_PR,
    SOURCE_IDLE,
    SOURCE_ORPHAN_OPEN_ISSUE,
    SOURCE_UNRESOLVED_DISCUSSION,
    select_next_task,
    select_next_task_with_ci_retry_guard,
)


logger = logging.getLogger(__name__)


__all__ = (
    "AutonomyProducer",
    "AutonomyProducerReport",
    "AutonomyDispatch",
    "AutonomyTickContext",
    "AUTONOMY_PRODUCER_HOLDER",
    "PRODUCER_TICK_LOCK_TTL_SECONDS",
    "DispatchOutcome",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Identifier used by the producer when grabbing locks. Stays a literal
# constant so dashboards / logs / tests can grep for it.
AUTONOMY_PRODUCER_HOLDER: str = "autonomy_producer"


# Default lock lifetime for one tick. The producer never holds a lock
# across ticks — this is a watchdog upper bound for callers that crash
# mid-tick. 30 s is comfortably longer than a single producer pass.
PRODUCER_TICK_LOCK_TTL_SECONDS: float = 30.0


# ---------------------------------------------------------------------------
# Outcome models
# ---------------------------------------------------------------------------


# Outcome string vocabulary surfaced via :class:`AutonomyDispatch`. Kept
# narrow on purpose so dashboards / tests can assert on the union.
class DispatchOutcome:
    DISPATCHED = "dispatched"
    DEDUPED = "deduped"
    LOCKED = "locked_by_other"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True)
class AutonomyDispatch:
    """One scheduling decision the producer made in a tick.

    *source* uses the same vocabulary as
    :mod:`next_task_selector` so the operator sees a unified surface
    in dashboards. *job_id* is the queue row the producer handed off
    to the worker (None when nothing was enqueued — the dispatch may
    still be informative, e.g. a deduped row).
    """

    source: str
    outcome: str
    session_id: str = ""
    executor_role: str = ""
    job_id: Optional[str] = None
    branch_hint: str = ""
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutonomyProducerReport:
    """Aggregate result of one :meth:`AutonomyProducer.tick` pass.

    Designed for log-once-per-tick summarisation — the producer's
    structured log line and the supervisor status post both reduce
    this report to a single sentence per source.
    """

    tick_id: str
    started_at: str
    finished_at: str
    next_task_candidate: Optional[NextTaskCandidate]
    dispatches: Tuple[AutonomyDispatch, ...] = ()
    locks_held: Tuple[str, ...] = ()
    error: Optional[str] = None

    def has_work(self) -> bool:
        if self.dispatches:
            return any(
                d.outcome == DispatchOutcome.DISPATCHED for d in self.dispatches
            )
        return False

    def summary_line(self) -> str:
        if self.error:
            return f"autonomy producer tick {self.tick_id} error={self.error}"
        if not self.dispatches:
            src = (
                self.next_task_candidate.source
                if self.next_task_candidate
                else SOURCE_IDLE
            )
            return f"autonomy producer tick {self.tick_id} idle (selector={src})"
        parts = [
            f"{d.source}={d.outcome}" + (f"/{d.executor_role}" if d.executor_role else "")
            for d in self.dispatches
        ]
        return f"autonomy producer tick {self.tick_id} " + ", ".join(parts)


@dataclass(frozen=True)
class AutonomyTickContext:
    """Per-tick view passed to sub-producers.

    Built once at the start of :meth:`AutonomyProducer.tick` so each
    sub-producer sees a consistent snapshot of "now", lock registry,
    and accumulator. Sub-producers append to ``dispatches`` in place
    via the :meth:`record` helper.
    """

    tick_id: str
    started_at: datetime
    locks: AutonomyLockRegistry
    dispatches: List[AutonomyDispatch] = field(default_factory=list)

    def record(self, dispatch: AutonomyDispatch) -> AutonomyDispatch:
        self.dispatches.append(dispatch)
        return dispatch


# ---------------------------------------------------------------------------
# AutonomyProducer
# ---------------------------------------------------------------------------


class AutonomyProducer:
    """Periodic scheduler that turns world state into queued work.

    Construction wires the producer to existing collaborators —
    every dependency is injectable so unit tests pass fakes and the
    runtime wires through production singletons.

    Typical lifecycle:

      1. ``producer = AutonomyProducer(session_state=..., coding_executor=..., ...)``
      2. The supervisor watch loop calls ``producer.tick()`` on its
         autonomy-tick interval.
      3. ``producer.tick()`` returns an :class:`AutonomyProducerReport`;
         the supervisor logs the summary line and (optionally) posts
         it to the runtime status board.

    The producer never blocks. If it can't acquire a lock it records
    a ``locked_by_other`` dispatch and moves on — the next tick re-tries.
    """

    def __init__(
        self,
        *,
        session_state: Any,
        coding_executor: CodingExecutorWorker,
        github_state: Optional[Any] = None,
        ci_retry_lookup: Optional[Callable[[int], Any]] = None,
        ci_retry_policy: Optional[Any] = None,
        lock_registry: Optional[AutonomyLockRegistry] = None,
        clock: Optional[Callable[[], datetime]] = None,
        env: Optional[Mapping[str, str]] = None,
        followup_dispatch: Optional[Callable[..., Any]] = None,
        completion_dispatch: Optional[Callable[..., Any]] = None,
        decision_port: Optional[Any] = None,
        idempotency_window_seconds: float = 60.0,
    ) -> None:
        self._session_state = session_state
        self._github_state = github_state
        self._coding_executor = coding_executor
        self._ci_retry_lookup = ci_retry_lookup
        self._ci_retry_policy = ci_retry_policy
        self._locks = lock_registry or AutonomyLockRegistry()
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        self._env = env
        self._followup_dispatch = followup_dispatch
        self._completion_dispatch = completion_dispatch
        self._decision_port = decision_port
        self._idem_window = max(1.0, float(idempotency_window_seconds))
        self._tick_counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def lock_registry(self) -> AutonomyLockRegistry:
        return self._locks

    def tick(self) -> AutonomyProducerReport:
        """Run one full scheduling pass.

        Order matters — we always poll the selector first so the
        report's ``next_task_candidate`` reflects the world view we
        acted on. Sub-producers run in their own try/except so a
        crash in one branch never poisons the others.
        """

        self._tick_counter += 1
        started = self._clock()
        tick_id = f"tick-{int(started.timestamp())}-{self._tick_counter}"
        ctx = AutonomyTickContext(
            tick_id=tick_id, started_at=started, locks=self._locks
        )

        candidate: Optional[NextTaskCandidate] = None
        error: Optional[str] = None
        try:
            candidate = self._poll_selector(ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "autonomy producer: selector poll raised", exc_info=True
            )
            error = f"selector_failed:{type(exc).__name__}"

        # Sub-producers — each runs even if the selector returned IDLE
        # because the selector is a *snapshot* and a sub-producer may
        # still find idempotent work to refresh.
        for runner_name, runner in self._sub_producers():
            try:
                runner(ctx, candidate=candidate)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "autonomy producer: sub-producer %s raised", runner_name,
                    exc_info=True,
                )
                ctx.record(
                    AutonomyDispatch(
                        source=runner_name,
                        outcome=DispatchOutcome.ERROR,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )

        finished = self._clock()
        report = AutonomyProducerReport(
            tick_id=tick_id,
            started_at=_iso(started),
            finished_at=_iso(finished),
            next_task_candidate=candidate,
            dispatches=tuple(ctx.dispatches),
            locks_held=tuple(self._locks.held_scopes().keys()),
            error=error,
        )
        return report

    # ------------------------------------------------------------------
    # Selector
    # ------------------------------------------------------------------

    def _poll_selector(self, ctx: AutonomyTickContext) -> NextTaskCandidate:
        """Run the next-task selector with the CI retry guard if available."""

        if self._github_state is None:
            return select_next_task(
                github_state=_NoGithubState(),
                session_state=self._session_state,
                now=ctx.started_at,
            )
        if self._ci_retry_lookup is not None:
            return select_next_task_with_ci_retry_guard(
                github_state=self._github_state,
                session_state=self._session_state,
                retry_lookup=self._ci_retry_lookup,
                policy=self._ci_retry_policy,
                now=ctx.started_at,
            )
        return select_next_task(
            github_state=self._github_state,
            session_state=self._session_state,
            now=ctx.started_at,
        )

    # ------------------------------------------------------------------
    # Sub-producer registry — order = priority within one tick.
    # ------------------------------------------------------------------

    def _sub_producers(
        self,
    ) -> Sequence[Tuple[str, Callable[..., None]]]:
        return (
            (SOURCE_APPROVED_CODING_JOB, self._produce_coding_executes),
            (SOURCE_UNRESOLVED_DISCUSSION, self._produce_discussion_followups),
            (SOURCE_CI_FAILED_PR, self._produce_ci_retry_followup),
        )

    # ------------------------------------------------------------------
    # Sub-producer 1: approved coding_jobs → coding_execute
    # ------------------------------------------------------------------

    def _produce_coding_executes(
        self,
        ctx: AutonomyTickContext,
        *,
        candidate: Optional[NextTaskCandidate],
    ) -> None:
        """Walk every ``coding_job=ready`` session and dispatch.

        Lock scope is per (session, role) — :func:`dispatch_ready_coding_jobs`
        already dedups against the worker queue, but the producer takes
        the lock so a concurrent tick (e.g. supervisor + per-job
        executor tick) doesn't both decide to enqueue the same row.
        """

        try:
            ready_rows = list(
                iter_ready_coding_jobs(
                    session_loader=getattr(self._session_state, "session_loader", None)
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "autonomy producer: iter_ready_coding_jobs raised", exc_info=True
            )
            return

        if not ready_rows:
            return

        for ready in ready_rows:
            scope = coding_job_scope(ready.session_id, ready.executor_role())
            lock = self._locks.acquire(
                scope,
                holder=AUTONOMY_PRODUCER_HOLDER,
                ttl_seconds=PRODUCER_TICK_LOCK_TTL_SECONDS,
            )
            if lock is None:
                ctx.record(
                    AutonomyDispatch(
                        source=SOURCE_APPROVED_CODING_JOB,
                        outcome=DispatchOutcome.LOCKED,
                        session_id=ready.session_id,
                        executor_role=ready.executor_role(),
                        reason=f"scope held: {scope}",
                    )
                )
                continue
            try:
                # Reuse the existing dispatcher's idempotent path. We
                # pass a single-row session_loader so the dispatcher
                # only acts on the current ready row.
                dispatched = dispatch_ready_coding_jobs(
                    worker=self._coding_executor,
                    session_loader=lambda r=ready: (r.session,),
                    update_session_fn=getattr(
                        self._session_state, "update_session_fn", None
                    ),
                    env=self._env,
                    now=ctx.started_at,
                )
            finally:
                self._locks.release(lock)

            for entry in dispatched:
                outcome = (
                    DispatchOutcome.DISPATCHED
                    if entry.created
                    else DispatchOutcome.DEDUPED
                )
                if entry.error:
                    outcome = DispatchOutcome.ERROR
                ctx.record(
                    AutonomyDispatch(
                        source=SOURCE_APPROVED_CODING_JOB,
                        outcome=outcome,
                        session_id=entry.session_id,
                        executor_role=entry.executor_role,
                        job_id=entry.job_id,
                        branch_hint=(
                            entry.request.branch_hint if entry.request else ""
                        ),
                        reason=entry.error or "",
                    )
                )

    # ------------------------------------------------------------------
    # Sub-producer 2: unresolved discussions → role_take / research_collect
    # ------------------------------------------------------------------

    def _produce_discussion_followups(
        self,
        ctx: AutonomyTickContext,
        *,
        candidate: Optional[NextTaskCandidate],
    ) -> None:
        """Hand unresolved discussion threads to the follow-up dispatcher.

        The actual enqueue logic lives in
        :mod:`discussion_followup` — we keep the producer thin so a
        new "discussion stays alive" rule lands as a one-line change
        there. The producer's job is to pick the right scope key,
        guard against parallel ticks, and turn the dispatcher's
        return into :class:`AutonomyDispatch` rows.
        """

        if self._followup_dispatch is None:
            return

        try:
            unresolved = list(
                self._session_state.list_unresolved_discussion_threads() or ()
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "autonomy producer: list_unresolved_discussion_threads raised",
                exc_info=True,
            )
            return

        for row in unresolved:
            session_id = str(row.get("session_id") or "")
            if not session_id:
                continue
            scope = session_scope(session_id)
            lock = self._locks.acquire(
                scope,
                holder=AUTONOMY_PRODUCER_HOLDER,
                ttl_seconds=PRODUCER_TICK_LOCK_TTL_SECONDS,
            )
            if lock is None:
                ctx.record(
                    AutonomyDispatch(
                        source=SOURCE_UNRESOLVED_DISCUSSION,
                        outcome=DispatchOutcome.LOCKED,
                        session_id=session_id,
                        reason=f"scope held: {scope}",
                    )
                )
                continue
            try:
                outcomes = self._followup_dispatch(
                    session_id=session_id,
                    discussion_row=row,
                    now=ctx.started_at,
                    decision_port=self._decision_port,
                )
            finally:
                self._locks.release(lock)

            for entry in outcomes or ():
                ctx.record(_normalize_dispatch(entry, session_id=session_id))

    # ------------------------------------------------------------------
    # Sub-producer 3: CI failed PR → completion-funnel hand-off
    # ------------------------------------------------------------------

    def _produce_ci_retry_followup(
        self,
        ctx: AutonomyTickContext,
        *,
        candidate: Optional[NextTaskCandidate],
    ) -> None:
        """Surface CI failure follow-ups to the completion funnel.

        The CI retry orchestrator already runs from the executor /
        run-service path. This sub-producer's job is to make sure the
        autonomy producer's report still mentions the CI funnel so the
        operator sees the full picture; the retry orchestrator owns
        the actual side effects.
        """

        if candidate is None or candidate.source != SOURCE_CI_FAILED_PR:
            return

        if self._completion_dispatch is None:
            ctx.record(
                AutonomyDispatch(
                    source=SOURCE_CI_FAILED_PR,
                    outcome=DispatchOutcome.SKIPPED,
                    reason="completion_dispatch not wired",
                    payload=dict(candidate.payload),
                )
            )
            return

        scope = branch_scope(
            str(candidate.payload.get("repo") or ""),
            str(candidate.payload.get("branch") or ""),
        )
        lock = self._locks.acquire(
            scope,
            holder=AUTONOMY_PRODUCER_HOLDER,
            ttl_seconds=PRODUCER_TICK_LOCK_TTL_SECONDS,
        )
        if lock is None:
            ctx.record(
                AutonomyDispatch(
                    source=SOURCE_CI_FAILED_PR,
                    outcome=DispatchOutcome.LOCKED,
                    reason=f"scope held: {scope}",
                    payload=dict(candidate.payload),
                )
            )
            return
        try:
            outcomes = self._completion_dispatch(
                candidate=candidate,
                now=ctx.started_at,
            )
        finally:
            self._locks.release(lock)

        for entry in outcomes or ():
            ctx.record(_normalize_dispatch(entry, source=SOURCE_CI_FAILED_PR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoGithubState:
    """Default :class:`GithubStateLike` impl when caller didn't wire one.

    Returns empty rows for every accessor — the producer's selector
    chain falls through to "approved coding_job → unresolved
    discussion → idle".
    """

    def list_failed_ci_active_prs(self) -> Sequence[Mapping[str, Any]]:
        return ()

    def list_open_issues_without_session(self) -> Sequence[Mapping[str, Any]]:
        return ()


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _normalize_dispatch(
    entry: Any,
    *,
    session_id: str = "",
    source: str = "",
) -> AutonomyDispatch:
    """Convert a sub-producer result into :class:`AutonomyDispatch`.

    Liberal in what it accepts so dispatchers can return either a
    fully-formed :class:`AutonomyDispatch` or a plain mapping that
    follows the same field names. Anything else lands as a generic
    ``skipped`` entry rather than crashing the producer.
    """

    if isinstance(entry, AutonomyDispatch):
        if not entry.session_id and session_id:
            return replace(entry, session_id=session_id)
        return entry
    if isinstance(entry, Mapping):
        return AutonomyDispatch(
            source=str(entry.get("source") or source or "unknown"),
            outcome=str(entry.get("outcome") or DispatchOutcome.DISPATCHED),
            session_id=str(entry.get("session_id") or session_id),
            executor_role=str(entry.get("executor_role") or entry.get("role") or ""),
            job_id=entry.get("job_id"),
            branch_hint=str(entry.get("branch_hint") or ""),
            reason=str(entry.get("reason") or ""),
            payload=dict(entry.get("payload") or {}),
        )
    return AutonomyDispatch(
        source=source or "unknown",
        outcome=DispatchOutcome.SKIPPED,
        session_id=session_id,
        reason=f"unsupported sub-producer return type: {type(entry).__name__}",
    )
