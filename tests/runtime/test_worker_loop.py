"""worker_loop — A-M6.0 unit tests.

Pin the long-running consumer loop contract: heartbeat fires,
filtered pick is used, no-job iterations sleep, exceptions don't
kill the loop, shutdown_event terminates cleanly, and the
supervisor watch loop drives sweep on a timer.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.job_queue.worker_loop import (
    run_supervisor_watch_loop,
    run_worker_loop,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)


class WorkerLoopBasicTests(_Fixture):
    def test_loop_picks_and_processes_then_exits_via_max_iterations(self) -> None:
        # Seed two jobs the loop should pick. ``max_iterations=4`` so
        # the loop runs at most 4 cycles (2 picks + maybe trailing
        # idle iterations) — bounds the test runtime even if pick
        # behaviour drifts.
        self.queue.enqueue(session_id="s1", job_type="research_collect")
        self.queue.enqueue(session_id="s2", job_type="research_collect")

        processed: List[str] = []

        async def process_job(job):
            processed.append(job.job_id)
            self.queue.transition(job.job_id, JobState.IN_PROGRESS)
            self.queue.transition(job.job_id, JobState.SAVED)

        async def fast_sleep(_secs):
            return None

        stats = _run(
            run_worker_loop(
                service_id="eng-research-worker",
                queue=self.queue,
                heartbeats=self.heartbeats,
                process_job=process_job,
                job_types=["research_collect"],
                sleep_fn=fast_sleep,
                max_iterations=4,
            )
        )
        self.assertEqual(stats.jobs_processed, 2)
        self.assertEqual(len(processed), 2)
        # Heartbeat landed at least once (interval=30s defaults; first
        # iteration always records since last_heartbeat_at=0).
        beat = self.heartbeats.get("eng-research-worker")
        self.assertIsNotNone(beat)

    def test_no_jobs_triggers_idle_sleep(self) -> None:
        sleep_calls = []

        async def fake_sleep(secs):
            sleep_calls.append(secs)

        async def process_job(_job):
            self.fail("process_job should not run when queue is empty")

        stats = _run(
            run_worker_loop(
                service_id="eng-approval-worker",
                queue=self.queue,
                heartbeats=self.heartbeats,
                process_job=process_job,
                job_types=["approval_post"],
                sleep_fn=fake_sleep,
                max_iterations=3,
                idle_sleep_seconds=0.123,
            )
        )
        # 3 iterations all idle → 3 sleep calls with the configured
        # idle interval.
        self.assertEqual(stats.idle_sleeps, 3)
        self.assertEqual(stats.jobs_processed, 0)
        self.assertTrue(all(c == 0.123 for c in sleep_calls))


class WorkerLoopRoleFilterTests(_Fixture):
    def test_role_loop_only_picks_its_role(self) -> None:
        # Two role_take jobs for different roles. Backend role loop
        # must claim only its own row and leave the other untouched.
        self.queue.enqueue(
            session_id="s1", job_type="role_take", role="ai-engineer"
        )
        self.queue.enqueue(
            session_id="s1", job_type="role_take", role="backend-engineer"
        )

        seen_roles: List[str] = []

        async def process_job(job):
            seen_roles.append(job.role)
            self.queue.transition(job.job_id, JobState.IN_PROGRESS)
            self.queue.transition(job.job_id, JobState.SAVED)

        async def fast_sleep(_secs):
            return None

        _run(
            run_worker_loop(
                service_id="eng-role-backend-engineer",
                queue=self.queue,
                heartbeats=self.heartbeats,
                process_job=process_job,
                job_types=["role_take"],
                roles=["backend-engineer"],
                sleep_fn=fast_sleep,
                max_iterations=3,
            )
        )
        self.assertEqual(seen_roles, ["backend-engineer"])
        # ai-engineer row stays QUEUED — loop never claimed it.
        ai_jobs = self.queue.list_for_session("s1", states=[JobState.QUEUED])
        self.assertEqual([j.role for j in ai_jobs], ["ai-engineer"])


class WorkerLoopExceptionIsolationTests(_Fixture):
    def test_process_job_exception_does_not_kill_loop(self) -> None:
        # Two jobs; the first raises, the second must still run.
        self.queue.enqueue(session_id="s1", job_type="research_collect")
        self.queue.enqueue(session_id="s2", job_type="research_collect")

        attempts: List[str] = []

        async def process_job(job):
            attempts.append(job.job_id)
            if len(attempts) == 1:
                # Even though we raise, the loop keeps going.
                # The worker's own process_job in real usage already
                # transitions to FAILED_RETRYABLE before re-raising;
                # here we transition manually to mimic that behaviour
                # so the row doesn't stay QUEUED and re-collide with
                # the next pick.
                self.queue.transition(job.job_id, JobState.ASSIGNED)
                self.queue.transition(
                    job.job_id, JobState.FAILED_RETRYABLE, clear_lease=True
                )
                raise RuntimeError("first job blew up")
            self.queue.transition(job.job_id, JobState.IN_PROGRESS)
            self.queue.transition(job.job_id, JobState.SAVED)

        async def fast_sleep(_secs):
            return None

        stats = _run(
            run_worker_loop(
                service_id="eng-research-worker",
                queue=self.queue,
                heartbeats=self.heartbeats,
                process_job=process_job,
                job_types=["research_collect"],
                sleep_fn=fast_sleep,
                max_iterations=4,
            )
        )
        # The loop survived the exception and processed the second job.
        self.assertEqual(len(attempts), 2)
        self.assertEqual(stats.jobs_processed, 1)
        self.assertEqual(stats.jobs_failed, 1)


class WorkerLoopShutdownTests(_Fixture):
    def test_shutdown_event_terminates_loop(self) -> None:
        self.queue.enqueue(session_id="s1", job_type="approval_post")

        async def process_job(_job):
            self.fail("loop should not iterate after shutdown_event is set")

        async def fake_sleep(_secs):
            return None

        async def driver():
            # Build the Event inside the coroutine so it binds to the
            # running loop (Python 3.9 asyncio.Event() outside a loop
            # raises RuntimeError when there's no current loop in the
            # main thread).
            shutdown = asyncio.Event()
            shutdown.set()
            return await run_worker_loop(
                service_id="eng-approval-worker",
                queue=self.queue,
                heartbeats=self.heartbeats,
                process_job=process_job,
                job_types=["approval_post"],
                shutdown_event=shutdown,
                sleep_fn=fake_sleep,
            )

        stats = _run(driver())
        # Loop must have exited immediately on shutdown_event check.
        self.assertEqual(stats.iterations, 0)


class SupervisorWatchLoopTests(_Fixture):
    def test_watch_loop_calls_sweep_each_iteration(self) -> None:
        sweep_calls: List[float] = []

        def fake_sweep(*, heartbeat_store, job_queue, deadline_seconds):
            from yule_orchestrator.agents.job_queue.heartbeat import (
                SupervisorSweepReport,
            )

            sweep_calls.append(deadline_seconds)
            return SupervisorSweepReport(
                stale=(), reaped_jobs=0, swept_at=0.0
            )

        async def fast_sleep(_secs):
            return None

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=fake_sweep,
                deadline_seconds=42.0,
                sweep_interval_seconds=0.0,
                sleep_fn=fast_sleep,
                max_iterations=3,
            )
        )
        self.assertEqual(iterations, 3)
        # Same deadline forwarded every iteration.
        self.assertEqual(sweep_calls, [42.0, 42.0, 42.0])

    def test_sweep_exception_does_not_kill_loop(self) -> None:
        attempts: List[int] = []

        def boom(**_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("sqlite blip")
            from yule_orchestrator.agents.job_queue.heartbeat import (
                SupervisorSweepReport,
            )

            return SupervisorSweepReport(
                stale=(), reaped_jobs=0, swept_at=0.0
            )

        async def fast_sleep(_secs):
            return None

        iterations = _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=boom,
                sleep_fn=fast_sleep,
                max_iterations=2,
            )
        )
        self.assertEqual(iterations, 2)
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
