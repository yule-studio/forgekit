"""role_take job worker — A-M4 wiring.

Member-bot research turns (open-call / chained dispatch / tech-lead
synthesis) used to run inside ``handle_research_turn_message`` as
direct in-process function calls. M4 routes the same logic through
the queue so each per-role take is a tracked ``role_take`` job:

  1. The member bot's ``on_message`` enters
     :func:`handle_research_turn_message`.
  2. After the cheap parse + dedup gates that already exist, the
     function asks :class:`RoleTakeWorker` to enqueue a ``role_take``
     job scoped to ``(session_id, role, kind)``.
  3. The same worker instance picks that job under a role filter so
     a backend-engineer worker never claims an ai-engineer row, then
     drives it through ``queued → assigned → in_progress → saved``.
  4. The runner body returns a :class:`ResearchTurnOutcome`. The
     member bot renders the outcome's ``message`` exactly as before —
     forum comment formatting is unchanged.

Sync, not async. The role-take render path is pure-Python; making
the worker sync keeps the Discord call site (also sync inside the
member bot's outcome handler) the same shape it had before M4. M3's
:class:`ResearchWorker` is async because the research collector
runner awaits providers; M4's runner doesn't, so we don't pretend
otherwise.

Dependency on research_collect: M4 *does not* wire a queue-level
``after_jobs`` edge. Instead, callers gate on ``research_pack``
existence before calling :meth:`run_one` (the open-call path
collects its own pack inside the runner, so it naturally bypasses
the gate). Full dependency wiring is a TODO for M5/M6 once the
gateway can hand the in-flight ``research_collect`` job id to the
member bot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .heartbeat import HeartbeatStore
from .state_machine import JobState
from .store import Job, JobQueue


JOB_TYPE_ROLE_TAKE: str = "role_take"

#: Per-role service id prefix. Combined with the role short id at
#: runtime so the supervisor sees one heartbeat row per active member
#: bot worker (e.g. ``eng-role-worker:backend-engineer``).
SERVICE_ID_ROLE_WORKER_PREFIX: str = "eng-role-worker"


# Kinds match the ``ROLE_TURN_KIND_*`` constants used by
# :func:`record_role_turn_event` in ``engineering_team_runtime``.
# Duplicated here as plain strings so this module doesn't pull in the
# discord layer.
KIND_OPEN: str = "open"
KIND_TURN: str = "turn"
KIND_SYNTHESIS: str = "synthesis"


_ACTIVE_STATES: Tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.ASSIGNED,
    JobState.IN_PROGRESS,
    JobState.WAITING_FOR_ROLE,
    JobState.RESEARCHING,
    JobState.PENDING_APPROVAL,
    JobState.READY_FOR_OBSIDIAN,
)


# What the member-bot side runner returns. Typed loosely so this
# module doesn't import the Discord runtime; callers cast the result
# to the expected :class:`ResearchTurnOutcome` shape on their side.
RoleTakeRunner = Callable[[Job], Any]


def service_id_for_role(role: str) -> str:
    """Return the heartbeat service_id for a role-scoped worker."""

    short = (role or "any").split("/", 1)[-1].strip() or "any"
    return f"{SERVICE_ID_ROLE_WORKER_PREFIX}:{short}"


@dataclass(frozen=True)
class RoleTakeJobOutcome:
    """Container the worker hands back to the caller.

    ``runner_result`` is whatever the runner returned (the member-bot
    layer reads it as :class:`ResearchTurnOutcome | None`).
    ``skipped_reason`` is set when ``run_one`` declined to start a
    fresh runner — most commonly because a duplicate job is already
    in flight for the same ``(session, role, kind)`` triple.
    """

    job: Optional[Job]
    runner_result: Optional[Any] = None
    skipped_reason: Optional[str] = None


class RoleTakeWorker:
    """Per-role queue worker for ``role_take`` jobs.

    A single :class:`RoleTakeWorker` instance is held by each member
    bot during M4. ``role_filter`` narrows which rows the pick step
    sees so a backend-engineer bot never claims an ai-engineer
    row — that contract becomes load-bearing in M6 when each role
    runs in its own systemd unit.
    """

    def __init__(
        self,
        *,
        queue: JobQueue,
        heartbeats: Optional[HeartbeatStore] = None,
        role_filter: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self._queue = queue
        self._heartbeats = heartbeats
        self._role_filter = role_filter
        if worker_id is not None:
            self._worker_id = worker_id
        else:
            short = (role_filter or "any").split("/", 1)[-1] or "any"
            self._worker_id = f"{SERVICE_ID_ROLE_WORKER_PREFIX}:{short}:{os.getpid()}"

    # ------------------------------------------------------------------
    # Producer — member bot calls this from on_message.
    # ------------------------------------------------------------------

    def find_active(
        self,
        *,
        session_id: str,
        role: str,
        kind: str,
    ) -> Optional[Job]:
        """Return the in-flight ``role_take`` job for the (session,
        role, kind) tuple, or None.

        Why scope to all three: an open-call take and a chained turn
        for the same role on the same session are *different*
        responses and should both be allowed. A repeat of the same
        kind, however, is the duplicate we need to drop.
        """

        if not session_id or not role or not kind:
            return None
        for job in self._queue.list_for_session(
            session_id, states=_ACTIVE_STATES
        ):
            if job.job_type != JOB_TYPE_ROLE_TAKE:
                continue
            if job.role != role:
                continue
            if (job.payload or {}).get("kind") != kind:
                continue
            return job
        return None

    def enqueue(
        self,
        *,
        session_id: str,
        role: str,
        kind: str,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> Tuple[Job, bool]:
        """Idempotent enqueue keyed on ``(session_id, role, kind)``.

        Returns ``(job, created)``. When *created* is False the
        caller looked up an existing in-flight job; the member bot
        treats that as "stay quiet — another producer already kicked
        it off".
        """

        existing = self.find_active(
            session_id=session_id, role=role, kind=kind
        )
        if existing is not None:
            return existing, False
        merged_payload = dict(payload or {})
        merged_payload.setdefault("kind", kind)
        job = self._queue.enqueue(
            session_id=session_id,
            job_type=JOB_TYPE_ROLE_TAKE,
            role=role,
            payload=merged_payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        return job, True

    # ------------------------------------------------------------------
    # Consumer — worker loop runs this per pick.
    # ------------------------------------------------------------------

    def process_job(
        self,
        job: Job,
        *,
        runner: RoleTakeRunner,
        now: Optional[float] = None,
    ) -> RoleTakeJobOutcome:
        """Drive *job* from ``assigned`` through ``saved`` (or
        ``failed_retryable``) using *runner* as the body.

        Records a heartbeat under ``eng-role-worker:<role>`` so the
        supervisor sees this role's worker is alive. The runner is
        invoked synchronously; if it raises, the job lands in
        ``failed_retryable`` with the error captured and the lease
        cleared so the M2 reaper / a future retry pass can take it.
        """

        if self._heartbeats is not None:
            try:
                self._heartbeats.record(
                    service_id_for_role(job.role or "any"),
                    pid=os.getpid(),
                    metadata={"job_id": job.job_id, "kind": (job.payload or {}).get("kind")},
                    now=now,
                )
            except Exception:  # noqa: BLE001 - heartbeat is observability only
                pass

        in_progress = self._queue.transition(
            job.job_id, JobState.IN_PROGRESS, now=now
        )
        try:
            result = runner(in_progress)
        except Exception as exc:  # noqa: BLE001 - error path
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
            result={"completed": True, "produced_outcome": bool(result)},
            clear_lease=True,
            now=now,
        )
        return RoleTakeJobOutcome(job=saved, runner_result=result)

    def run_one(
        self,
        *,
        session_id: str,
        role: str,
        kind: str,
        runner: RoleTakeRunner,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> RoleTakeJobOutcome:
        """Producer + consumer single-shot helper.

        Steps:

          1. Idempotent ``enqueue`` keyed on ``(session_id, role,
             kind)``. Existing in-flight job → ``skipped_reason =
             "duplicate_in_flight"``, runner is **not** called.
          2. ``pick`` to claim the lease, narrowed to the worker's
             role filter so cross-role workers can't grab the row.
          3. ``process_job`` runs the runner under the lease.
        """

        if not session_id:
            raise ValueError("session_id is required")
        if not role:
            raise ValueError("role is required")
        if not kind:
            raise ValueError("kind is required")

        # Role filter mismatch is a programmer error — surface loudly
        # so an unscoped worker can't accidentally drive a
        # different-role job through process_job.
        if self._role_filter is not None and role != self._role_filter:
            raise ValueError(
                f"worker role_filter={self._role_filter!r} cannot "
                f"run_one for role={role!r}"
            )

        job, created = self.enqueue(
            session_id=session_id,
            role=role,
            kind=kind,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )
        if not created:
            return RoleTakeJobOutcome(
                job=job,
                runner_result=None,
                skipped_reason="duplicate_in_flight",
            )

        roles: Sequence[str] = (role,) if self._role_filter is None else (self._role_filter,)
        picked = self._queue.pick(
            worker_id=self._worker_id,
            job_types=[JOB_TYPE_ROLE_TAKE],
            roles=roles,
            now=now,
        )
        if picked is None or picked.job_id != job.job_id:
            return RoleTakeJobOutcome(
                job=picked or job,
                runner_result=None,
                skipped_reason="claimed_by_other_worker",
            )

        return self.process_job(picked, runner=runner, now=now)


def _short_error(exc: BaseException) -> str:
    """One-line error string used as the ``failed_retryable`` result.

    Same shape as M3's ``ResearchWorker._short_error`` so the
    supervisor diagnostic can surface either failure mode with one
    formatter.
    """

    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:500]


__all__ = (
    "JOB_TYPE_ROLE_TAKE",
    "KIND_OPEN",
    "KIND_SYNTHESIS",
    "KIND_TURN",
    "RoleTakeJobOutcome",
    "RoleTakeRunner",
    "RoleTakeWorker",
    "SERVICE_ID_ROLE_WORKER_PREFIX",
    "service_id_for_role",
)
