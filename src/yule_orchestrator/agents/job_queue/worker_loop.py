"""Long-running worker loop helper — A-M6.0.

Each queue worker (research / role take / approval / obsidian writer)
already has a ``process_job`` method that drives one row through
``assigned → in_progress → saved`` (or ``failed_retryable``). M6.0
wraps those into an infinite consumer loop:

  1. Record a heartbeat (capped at ``heartbeat_interval`` so the
     loop body itself stays cheap).
  2. ``queue.pick(...)`` with the worker's filters (``job_types`` /
     ``roles``).
  3. No job → sleep ``idle_sleep_seconds``.
  4. Job → ``await process_job(job)``. The worker's body has its own
     ``try/except`` that drives the row to ``failed_retryable`` on
     failure; the loop additionally swallows the exception so a
     buggy single job can't kill the whole process.
  5. ``shutdown_event.is_set()`` ends the loop after the current
     iteration finishes.

Sleep + heartbeat clock + job dispatch are all injectable so the
unit tests can drive a finite number of iterations without real
sleeps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Sequence,
)

from .heartbeat import HeartbeatStore
from .store import Job, JobQueue


logger = logging.getLogger(__name__)


# Defaults tuned for the always-on engineering runtime. The loop is
# forgiving — pick is cheap (one indexed SELECT) so a 1 s idle sleep
# keeps responsiveness high without burning CPU when the queue is
# quiet. Heartbeat interval defaults to 30 s to match
# ``DEFAULT_HEARTBEAT_INTERVAL_SECONDS``.
DEFAULT_IDLE_SLEEP: float = 1.0
DEFAULT_HEARTBEAT_INTERVAL: float = 30.0


# Test seam: an asyncio.Event-like that the loop checks each
# iteration. Real usage gets a real ``asyncio.Event``; tests pass
# a stub whose ``is_set()`` returns True after N iterations.
class _ShutdownProbe:
    def is_set(self) -> bool:  # pragma: no cover - protocol stub
        ...

    async def wait(self) -> Any:  # pragma: no cover - protocol stub
        ...


@dataclass(frozen=True)
class WorkerLoopStats:
    """Counters returned when the loop exits.

    Useful for tests + the supervisor diagnostic so a future operator
    can see "this worker processed N jobs / hit N exceptions / did N
    idle sleeps" without scraping logs.
    """

    iterations: int
    jobs_processed: int
    jobs_failed: int
    idle_sleeps: int


async def run_worker_loop(
    *,
    service_id: str,
    queue: JobQueue,
    heartbeats: Optional[HeartbeatStore],
    process_job: Callable[[Job], Awaitable[Any]],
    job_types: Sequence[str] = (),
    roles: Sequence[str] = (),
    shutdown_event: Optional[asyncio.Event] = None,
    idle_sleep_seconds: float = DEFAULT_IDLE_SLEEP,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL,
    sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
    now_fn: Optional[Callable[[], float]] = None,
    max_iterations: Optional[int] = None,
    pick_lease_seconds: float = 60.0,
) -> WorkerLoopStats:
    """Infinite consumer loop. Returns when ``shutdown_event`` fires
    or ``max_iterations`` is reached (the latter is a test-only knob).

    *process_job* is the same callable each worker uses today —
    typically ``ApprovalWorker.process_job`` or a closure that calls
    it. The loop never re-raises out of *process_job*; if the job's
    own state-machine transition fails the supervisor will see the
    row stuck and the lease reaper will recover it.
    """

    sleep_fn = sleep_fn or asyncio.sleep
    now_fn = now_fn or time.time

    iterations = 0
    jobs_processed = 0
    jobs_failed = 0
    idle_sleeps = 0
    last_heartbeat_at: float = 0.0

    def _shutdown_requested() -> bool:
        return shutdown_event is not None and shutdown_event.is_set()

    while True:
        if _shutdown_requested():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break

        iterations += 1

        # 1. heartbeat — gated by interval so the loop body is cheap
        #    even when picks fire every 100 ms.
        if heartbeats is not None:
            current = now_fn()
            if current - last_heartbeat_at >= heartbeat_interval_seconds:
                try:
                    heartbeats.record(service_id, now=current)
                except Exception:  # noqa: BLE001 - heartbeat is observability only
                    logger.warning(
                        "heartbeat record failed for %s", service_id, exc_info=True
                    )
                last_heartbeat_at = current

        # 2. pick a job under this worker's filters.
        try:
            job = queue.pick(
                worker_id=service_id,
                job_types=tuple(job_types),
                roles=tuple(roles),
                lease_seconds=pick_lease_seconds,
            )
        except Exception:  # noqa: BLE001 - SQLite blip; sleep + retry
            logger.warning(
                "queue pick failed for %s", service_id, exc_info=True
            )
            await sleep_fn(idle_sleep_seconds)
            idle_sleeps += 1
            continue

        # 3. nothing to do → sleep.
        if job is None:
            await sleep_fn(idle_sleep_seconds)
            idle_sleeps += 1
            continue

        # 4. process the job. The worker's process_job already drives
        #    the row to ``saved`` / ``failed_retryable``. We only need
        #    to swallow exceptions so a bad job doesn't kill the loop.
        try:
            await process_job(job)
            jobs_processed += 1
        except Exception:  # noqa: BLE001 - per-job error is recoverable
            jobs_failed += 1
            logger.warning(
                "process_job raised for %s job=%s", service_id, job.job_id, exc_info=True
            )

    return WorkerLoopStats(
        iterations=iterations,
        jobs_processed=jobs_processed,
        jobs_failed=jobs_failed,
        idle_sleeps=idle_sleeps,
    )


# ---------------------------------------------------------------------------
# Supervisor watch loop — wraps ``run_supervisor_sweep`` on a timer
# ---------------------------------------------------------------------------


async def run_supervisor_watch_loop(
    *,
    heartbeat_store: HeartbeatStore,
    job_queue: JobQueue,
    sweep_fn: Optional[Callable[..., Any]] = None,
    deadline_seconds: float = 90.0,
    sweep_interval_seconds: float = 5.0,
    shutdown_event: Optional[asyncio.Event] = None,
    sleep_fn: Optional[Callable[[float], Awaitable[None]]] = None,
    max_iterations: Optional[int] = None,
    on_sweep: Optional[Callable[[Any], None]] = None,
    status_post_fn: Optional[Callable[[], Awaitable[Any]]] = None,
    status_post_interval_seconds: Optional[float] = None,
    time_fn: Optional[Callable[[], float]] = None,
) -> int:
    """Long-running supervisor watchdog. Calls
    :func:`run_supervisor_sweep` every *sweep_interval_seconds* and
    surfaces the stale-services / reaped-jobs counts via *on_sweep*
    (defaults to a stdlib logger info line).

    A-M7-final added optional status posting:

      * *status_post_fn* — coroutine that builds + posts the
        ``#봇-상태`` markdown summary. Production wires it to a
        closure around ``runtime.status_poster.post_runtime_status_summary``;
        tests inject a stub.
      * *status_post_interval_seconds* — minimum spacing between
        post attempts (each attempt is dedup-checked, so identical
        states naturally don't repost). When ``None`` the post
        path is dormant — supervisor still ticks but never posts.
      * *time_fn* — clock for the post-interval gate. Tests pass
        a monotonic counter so they can fast-forward without real
        wall time.

    Posting failures are caught + logged; they NEVER crash the
    supervisor. The dedup state lives inside *status_post_fn* (the
    poster's own state store) so this loop only owns the cadence.
    """

    if sweep_fn is None:
        from .heartbeat import run_supervisor_sweep as _default_sweep

        sweep_fn = _default_sweep

    sleep_fn = sleep_fn or asyncio.sleep
    clock = time_fn or time.time
    iterations = 0
    last_post_at: Optional[float] = None
    post_enabled = (
        status_post_fn is not None
        and status_post_interval_seconds is not None
        and status_post_interval_seconds > 0
    )

    def _shutdown_requested() -> bool:
        return shutdown_event is not None and shutdown_event.is_set()

    while True:
        if _shutdown_requested():
            break
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1
        try:
            report = sweep_fn(
                heartbeat_store=heartbeat_store,
                job_queue=job_queue,
                deadline_seconds=deadline_seconds,
            )
        except Exception:  # noqa: BLE001 - never crash the supervisor
            logger.warning("supervisor sweep failed", exc_info=True)
            await sleep_fn(sweep_interval_seconds)
            continue

        if on_sweep is not None:
            try:
                on_sweep(report)
            except Exception:  # noqa: BLE001 - logging hook is best-effort
                logger.warning("supervisor on_sweep handler raised", exc_info=True)
        else:
            stale_count = len(getattr(report, "stale", ()) or ())
            reaped = int(getattr(report, "reaped_jobs", 0) or 0)
            if stale_count or reaped:
                logger.info(
                    "supervisor sweep — stale=%d reaped=%d",
                    stale_count,
                    reaped,
                )

        # ----- A-M7-final: status posting tick -----------------------
        if post_enabled:
            now_clock = float(clock())
            interval = float(status_post_interval_seconds or 0.0)
            if (
                last_post_at is None
                or (now_clock - last_post_at) >= interval
            ):
                try:
                    await status_post_fn()  # type: ignore[misc]
                except Exception:  # noqa: BLE001 - never crash supervisor
                    logger.warning(
                        "supervisor status post raised", exc_info=True
                    )
                # Always advance the gate, success or not — a
                # transient outage shouldn't make the loop hammer
                # Discord on every sweep tick. The poster itself
                # has dedup so a "missed" interval is recovered on
                # the next state change anyway.
                last_post_at = now_clock

        await sleep_fn(sweep_interval_seconds)

    return iterations


__all__ = (
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_IDLE_SLEEP",
    "WorkerLoopStats",
    "run_supervisor_watch_loop",
    "run_worker_loop",
)
