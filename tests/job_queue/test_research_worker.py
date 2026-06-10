"""ResearchWorker — A-M3 wiring tests.

Pin the contract that research collection now flows through the
queue: each gateway call lands as a ``research_collect`` job,
duplicates for the same session are dropped, the worker drives the
job through ``queued → assigned → in_progress → saved`` on success,
and runner exceptions land the job in ``failed_retryable`` (so the
M2 reaper / a future retry pass can pick it up again).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
    SERVICE_ID_RESEARCH_WORKER,
    ResearchWorker,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


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
        self.worker = ResearchWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )


class EnqueuePathTests(_Fixture):
    """Enqueue creates a research_collect job, then dedup blocks
    duplicates while the first one is in-flight.

    Why this matters: the live MVP regression had the gateway run a
    second collect every time the user re-confirmed an already-running
    intake. M3 makes that idempotent at the queue layer, so the
    "이미 진행 중" message comes for free.
    """

    def test_enqueue_creates_research_collect_job(self) -> None:
        job, created = self.worker.enqueue(session_id="sess-1")
        self.assertTrue(created)
        self.assertEqual(job.job_type, JOB_TYPE_RESEARCH_COLLECT)
        self.assertEqual(job.state, JobState.QUEUED)
        self.assertEqual(job.session_id, "sess-1")

    def test_enqueue_returns_existing_active_job(self) -> None:
        first, _ = self.worker.enqueue(session_id="sess-1")
        second, created = self.worker.enqueue(session_id="sess-1")
        # Dedup must point at the same row, and the second caller
        # gets ``created=False`` so the gateway can render a
        # "이미 진행 중" notice instead of doubling up.
        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(created)

    def test_terminal_jobs_do_not_block_new_enqueue(self) -> None:
        first, _ = self.worker.enqueue(session_id="sess-1")
        # Drive first job to a terminal state — a re-collect on the
        # same session must be allowed afterwards.
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)
        self.queue.transition(first.job_id, JobState.SAVED)
        second, created = self.worker.enqueue(session_id="sess-1")
        self.assertTrue(created)
        self.assertNotEqual(first.job_id, second.job_id)


class WorkerSuccessPathTests(_Fixture):
    """``run_one`` drives queued → assigned → in_progress → saved on
    a happy runner. Heartbeat is recorded so the supervisor sweep
    sees the worker as alive.
    """

    def test_success_walks_state_machine_through_saved(self) -> None:
        async def runner(_job):
            return {"forum_thread_id": 1234}

        outcome = _run(
            self.worker.run_one(session_id="sess-1", runner=runner)
        )
        # No skip — the runner ran and the job ended up SAVED.
        self.assertIsNone(outcome.skipped_reason)
        self.assertIsNotNone(outcome.job)
        assert outcome.job is not None  # mypy
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # The runner result is forwarded so the gateway can build its
        # EngineeringResearchLoopReport without going back to the queue.
        self.assertEqual(
            outcome.runner_result, {"forum_thread_id": 1234}
        )
        # ``picked_by`` was cleared by the SAVED transition so the
        # next pick of any future job for this session doesn't think
        # the old worker is still on it.
        self.assertIsNone(outcome.job.picked_by)

    def test_success_records_heartbeat(self) -> None:
        async def runner(_job):
            return None

        _run(self.worker.run_one(session_id="sess-1", runner=runner))
        beat = self.heartbeats.get(SERVICE_ID_RESEARCH_WORKER)
        # Heartbeat must land so the supervisor sweep sees the
        # research worker as alive (vs. a never-registered service).
        self.assertIsNotNone(beat)

    def test_duplicate_call_returns_skipped_reason(self) -> None:
        # Manually plant an in-flight job so run_one's enqueue dedup
        # fires before pick. Mirrors the live "user re-types intake
        # while collect is still running" path.
        first, _ = self.worker.enqueue(session_id="sess-1")
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)

        runner_called = False

        async def runner(_job):
            nonlocal runner_called
            runner_called = True
            return None

        outcome = _run(
            self.worker.run_one(session_id="sess-1", runner=runner)
        )
        # Runner must NOT be called when a duplicate is in flight —
        # that would re-run the collector and spend the search budget
        # twice.
        self.assertFalse(runner_called)
        self.assertEqual(outcome.skipped_reason, "duplicate_in_flight")


class WorkerRetryablePathTests(_Fixture):
    """When the runner raises, the job lands in failed_retryable
    with the lease cleared so a later worker can retry without
    waiting for the lease reaper to kick in.
    """

    def test_runner_exception_moves_job_to_failed_retryable(self) -> None:
        async def boom(_job):
            raise RuntimeError("provider 429 throttle")

        with self.assertRaises(RuntimeError):
            _run(self.worker.run_one(session_id="sess-1", runner=boom))

        # The job must now be queryable in failed_retryable state
        # with a captured error message, lease cleared, and ready
        # for either the M2 reaper or a manual ``requeue_retryable``
        # to take it through to a retry attempt.
        jobs = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(jobs), 1)
        failed = jobs[0]
        self.assertIsNone(failed.picked_by)
        self.assertIsNone(failed.picked_until)
        self.assertIn("RuntimeError", failed.result.get("error", ""))
        self.assertIn("provider 429 throttle", failed.result.get("error", ""))

    def test_failed_retryable_can_be_requeued(self) -> None:
        async def boom(_job):
            raise RuntimeError("transient")

        with self.assertRaises(RuntimeError):
            _run(self.worker.run_one(session_id="sess-1", runner=boom))

        jobs = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        assert jobs
        # M2 contract: failed_retryable → queued (with backoff +
        # attempt++) is the normal recovery path. The worker doesn't
        # do this itself — supervisor sweep / a deliberate operator
        # action does.
        requeued = self.queue.requeue_retryable(
            jobs[0].job_id, backoff_seconds=0
        )
        self.assertEqual(requeued.state, JobState.QUEUED)
        self.assertEqual(requeued.attempt, 1)


class WorkerDirectProcessJobTests(_Fixture):
    """``process_job`` exposed separately so M6's standalone
    worker process can pick a job and run it without going through
    ``run_one``'s producer-consumer combo.
    """

    def test_process_job_walks_state_machine(self) -> None:
        # Seed a queued job + simulate the pick step ourselves so we
        # can call process_job directly — this is the path M6's
        # standalone worker will take in its consumer loop.
        seed, _ = self.worker.enqueue(session_id="sess-1")
        picked = self.queue.pick(
            worker_id="external-worker",
            job_types=[JOB_TYPE_RESEARCH_COLLECT],
        )
        assert picked is not None

        async def runner(_job):
            return {"ok": True}

        outcome = _run(self.worker.process_job(picked, runner=runner))
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)


if __name__ == "__main__":
    unittest.main()
