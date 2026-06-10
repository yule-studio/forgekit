"""research_collect job worker — A-M3 wiring.

The gateway used to call ``research_loop_fn`` directly inside its
``on_message`` handler. M3 routes the same call through the queue:

  1. The gateway enqueues a ``research_collect`` job for the session.
  2. :class:`ResearchWorker` picks that job (atomic lease via
     :meth:`JobQueue.pick`), records a heartbeat, runs the existing
     research-loop runner, and transitions the job to ``saved``
     (or ``failed_retryable`` if the runner raised).
  3. Discord-side artifacts (``follow_up_message``, ``forum_status_message``,
     ``session.extra`` updates) keep their previous behaviour — the
     worker only adds state-machine framing around the same call.

This is the *in-process* adapter so M3 lands without splitting
processes. M6 introduces a long-running standalone worker that picks
from the same queue with the same contract; nothing about the
producer side needs to change at that point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue


JOB_TYPE_RESEARCH_COLLECT: str = "research_collect"
SERVICE_ID_RESEARCH_WORKER: str = "eng-research-worker"


# Active states that mean "another process is already on this session's
# research_collect" — used by the idempotent ``enqueue`` to skip a
# duplicate request. ``waiting_for_role`` would be a fan-in pattern that
# research jobs don't use, but we list it for completeness so a future
# composite job can't slip past dedup either.
_ACTIVE_STATES: Tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.WAITING_FOR_ROLE,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
)


# What the runner returns. The router builds an
# ``EngineeringResearchLoopReport`` from this — the worker stays
# decoupled from the discord layer so M6 can run it with no Discord
# import in the loop.
RunnerCallable = Callable[[Job], Awaitable[Any]]


@dataclass(frozen=True)
class ResearchJobOutcome:
    """Container the worker hands back to the caller.

    ``runner_result`` is whatever the runner returned (the router
    coerces it into ``EngineeringResearchLoopReport``).
    ``job`` is the post-transition row so the caller can read
    ``state`` / ``attempt`` / ``picked_by`` for its own observability.
    ``skipped_reason`` is set when the worker chose not to run because
    another instance was already on the session — Discord then shows
    a "이미 진행 중" friendly notice instead of double-running.
    """

    job: Optional[Job]
    runner_result: Optional[Any] = None
    skipped_reason: Optional[str] = None


class ResearchWorker:
    """Idempotent in-process worker for ``research_collect`` jobs.

    Held by the gateway during M3. The same class also works as the
    body of a standalone worker process in M6 — only the loop driver
    around :meth:`run_one` changes (CLI ``yule run-service
    research-worker`` instead of being called directly from
    ``on_message``).
    """

    def __init__(
        self,
        *,
        queue: JobQueue,
        heartbeats: Optional[HeartbeatStore] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self._queue = queue
        self._heartbeats = heartbeats
        # ``worker_id`` is what lands in ``picked_by`` when this worker
        # claims a job. Default mirrors the systemd service id
        # convention so journalctl + the queue audit trail line up.
        self._worker_id = worker_id or f"{SERVICE_ID_RESEARCH_WORKER}:{os.getpid()}"

    # ------------------------------------------------------------------
    # Producer side — gateway calls this from intake / continuation.
    # ------------------------------------------------------------------

    def find_active(self, session_id: str) -> Optional[Job]:
        """Return any non-terminal ``research_collect`` job for *session_id*.

        Used by :meth:`enqueue` and :meth:`run_one` to make duplicate
        intakes idempotent — if the session already has an in-flight
        collect, we don't fan out a second one.
        """

        if not session_id:
            return None
        for job in self._queue.list_for_session(
            session_id, states=_ACTIVE_STATES
        ):
            if job.job_type == JOB_TYPE_RESEARCH_COLLECT:
                return job
        return None

    def enqueue(
        self,
        *,
        session_id: str,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> Tuple[Job, bool]:
        """Idempotent enqueue.

        Returns ``(job, created)``. When *created* is False the caller
        looked up an existing in-flight job; this is the dedup signal
        the gateway uses to send "이미 진행 중" instead of starting
        another collect.
        """

        existing = self.find_active(session_id)
        if existing is not None:
            return existing, False
        job = self._queue.enqueue(
            session_id=session_id,
            job_type=JOB_TYPE_RESEARCH_COLLECT,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        return job, True

    # ------------------------------------------------------------------
    # Consumer side — worker loop runs this per pick.
    # ------------------------------------------------------------------

    async def process_job(
        self,
        job: Job,
        *,
        runner: RunnerCallable,
        now: Optional[float] = None,
    ) -> ResearchJobOutcome:
        """Drive *job* from ``assigned`` through ``saved`` (or
        ``failed_retryable``) using *runner* as the actual work body.

        Records a heartbeat for ``eng-research-worker`` so the
        supervisor sees the worker is live for the duration of the
        run. The runner is awaited under the queue's lease — if the
        worker process dies mid-run, the M2 reaper picks the job back
        up after ``picked_until`` expires.
        """

        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    SERVICE_ID_RESEARCH_WORKER,
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id},
                    now=now,
                )
            except Exception:  # noqa: BLE001 - heartbeat is observability only
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )

        try:
            result = await runner(in_progress)
        except Exception as exc:  # noqa: BLE001 - error path
            # Surface the exception type + first line of the message so
            # an operator scanning ``role_research_results`` /
            # supervisor diagnostic can see why the job was retried.
            self._queue.transition(
                in_progress.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": _short_error(exc)},
                clear_lease=True,
                now=now,
            )
            raise

        saved = self._queue.transition(
            in_progress.job_id,
            JobState.SAVED,
            result={"completed": True},
            clear_lease=True,
            now=now,
        )
        return ResearchJobOutcome(job=saved, runner_result=result)

    async def run_one(
        self,
        *,
        session_id: str,
        runner: RunnerCallable,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> ResearchJobOutcome:
        """Producer + consumer single-shot helper used by the gateway.

        Steps:

          1. Idempotent ``enqueue``. If a duplicate is already
             in-flight, returns a skipped outcome — the gateway shows
             "이미 진행 중" to the user.
          2. ``pick`` to claim the lease. If somebody else snatched it
             between (1) and (2) — possible once M6 introduces a
             standalone worker — return a skipped outcome.
          3. ``process_job`` runs the runner under the lease.

        M3 keeps everything in one Python process, so the pick race
        in step 2 is theoretical; surfacing it now means M6 doesn't
        need to add a new code path.
        """

        if not session_id:
            raise ValueError("session_id is required")

        job, created = self.enqueue(
            session_id=session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        if not created:
            return ResearchJobOutcome(
                job=job,
                runner_result=None,
                skipped_reason="duplicate_in_flight",
            )

        picked = self._queue.pick(
            worker_id=self._worker_id,
            job_types=[JOB_TYPE_RESEARCH_COLLECT],
            now=now,
        )
        if picked is None or picked.job_id != job.job_id:
            # Another worker claimed our row first. Don't compete —
            # the other worker will drive it through to saved on its
            # own. The gateway sees a skipped outcome and stays quiet.
            return ResearchJobOutcome(
                job=picked or job,
                runner_result=None,
                skipped_reason="claimed_by_other_worker",
            )

        return await self.process_job(picked, runner=runner, now=now)


def _short_error(exc: BaseException) -> str:
    """One-line error string used as the ``failed_retryable`` result.

    Keeps the queue's ``result_json`` compact so a long stack trace
    doesn't bloat the row. The full traceback still goes through the
    caller's logging path; this field is just for the audit trail.
    """

    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:500]


__all__ = (
    "JOB_TYPE_RESEARCH_COLLECT",
    "SERVICE_ID_RESEARCH_WORKER",
    "ResearchJobOutcome",
    "ResearchWorker",
    "RunnerCallable",
)
