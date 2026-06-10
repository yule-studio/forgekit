"""RoleTakeWorker — A-M4 wiring tests.

Pin the contract that role takes (open-call / chained dispatch /
synthesis) flow through the queue: each gateway/member-bot call
lands as a ``role_take`` job scoped to ``(session, role, kind)``,
duplicates are dropped, role-filtered workers only see their own
rows, and the runner runs under ``queued → assigned → in_progress
→ saved`` on success or ``failed_retryable`` on exception.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
    KIND_SYNTHESIS,
    KIND_TURN,
    RoleTakeWorker,
    service_id_for_role,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)
        self.worker = RoleTakeWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )


class EnqueueDedupTests(_Fixture):
    def test_enqueue_creates_role_take_job(self) -> None:
        job, created = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        self.assertTrue(created)
        self.assertEqual(job.job_type, JOB_TYPE_ROLE_TAKE)
        self.assertEqual(job.role, "ai-engineer")
        self.assertEqual(job.payload.get("kind"), KIND_OPEN)
        self.assertEqual(job.state, JobState.QUEUED)

    def test_dedup_keys_on_session_role_kind_triple(self) -> None:
        first, _ = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        # Same (session, role, kind) → dedup hits.
        second, created = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        self.assertEqual(first.job_id, second.job_id)
        self.assertFalse(created)

    def test_different_kind_for_same_role_is_not_dedup(self) -> None:
        # An open-call take and a chained turn take for the same role
        # are *different* responses — both should be allowed in flight.
        first, created_open = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        second, created_turn = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_TURN
        )
        self.assertTrue(created_open)
        self.assertTrue(created_turn)
        self.assertNotEqual(first.job_id, second.job_id)

    def test_terminal_jobs_do_not_block_new_enqueue(self) -> None:
        first, _ = self.worker.enqueue(
            session_id="sess-1", role="qa-engineer", kind=KIND_OPEN
        )
        # Drive first job to SAVED so a re-collect on the same triple
        # is allowed afterwards (e.g. operator re-kicks the open call).
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)
        self.queue.transition(first.job_id, JobState.SAVED)
        second, created = self.worker.enqueue(
            session_id="sess-1", role="qa-engineer", kind=KIND_OPEN
        )
        self.assertTrue(created)
        self.assertNotEqual(first.job_id, second.job_id)


class RoleFilterPickTests(_Fixture):
    def test_role_scoped_worker_only_picks_its_role(self) -> None:
        # Two queued jobs for different roles. A backend-engineer
        # worker must not claim the ai-engineer row — that's the
        # contract M6's standalone systemd units rely on. We exercise
        # the pick path directly (not run_one) so dedup doesn't
        # interfere with what we're trying to prove.
        self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        self.worker.enqueue(
            session_id="sess-1", role="backend-engineer", kind=KIND_OPEN
        )

        # Backend worker only sees ``backend-engineer`` rows.
        picked = self.queue.pick(
            worker_id="backend-worker",
            job_types=[JOB_TYPE_ROLE_TAKE],
            roles=["backend-engineer"],
        )
        self.assertIsNotNone(picked)
        assert picked is not None  # mypy
        self.assertEqual(picked.role, "backend-engineer")

        # ai-engineer row is still queued — backend pick did not
        # touch it.
        ai_jobs = self.queue.list_for_session(
            "sess-1", states=[JobState.QUEUED]
        )
        roles_left_queued = {j.role for j in ai_jobs}
        self.assertIn("ai-engineer", roles_left_queued)
        self.assertNotIn("backend-engineer", roles_left_queued)

    def test_role_mismatch_in_run_one_raises(self) -> None:
        # Programmer error: a backend-scoped worker asked to run an
        # ai-engineer job. Better to surface loudly than to silently
        # mis-claim — surface as ValueError.
        backend = RoleTakeWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            role_filter="backend-engineer",
        )

        def runner(_job):
            return None

        with self.assertRaises(ValueError):
            backend.run_one(
                session_id="sess-1",
                role="ai-engineer",
                kind=KIND_OPEN,
                runner=runner,
            )


class WorkerSuccessPathTests(_Fixture):
    def test_success_walks_state_machine_through_saved(self) -> None:
        called_with: dict = {}

        def runner(job):
            called_with["job_id"] = job.job_id
            called_with["role"] = job.role
            return "rendered take"

        outcome = self.worker.run_one(
            session_id="sess-1",
            role="qa-engineer",
            kind=KIND_TURN,
            runner=runner,
        )
        self.assertIsNone(outcome.skipped_reason)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)
        # Runner result forwards as-is — the member bot reads this as
        # the rendered ResearchTurnOutcome and posts the comment.
        self.assertEqual(outcome.runner_result, "rendered take")
        # The runner was invoked while the row was in_progress so
        # ``job.role`` was populated when the body ran.
        self.assertEqual(called_with["role"], "qa-engineer")

    def test_success_records_per_role_heartbeat(self) -> None:
        def runner(_job):
            return "ok"

        self.worker.run_one(
            session_id="sess-1",
            role="devops-engineer",
            kind=KIND_OPEN,
            runner=runner,
        )
        # Heartbeat must land under the role-scoped service id so
        # the supervisor sees one row per active role worker (vs.
        # one shared row for "all role workers").
        beat = self.heartbeats.get(service_id_for_role("devops-engineer"))
        self.assertIsNotNone(beat)

    def test_duplicate_call_skips_runner(self) -> None:
        # Plant an in-flight job so run_one's enqueue dedup fires.
        first, _ = self.worker.enqueue(
            session_id="sess-1", role="ai-engineer", kind=KIND_OPEN
        )
        self.queue.transition(first.job_id, JobState.ASSIGNED)
        self.queue.transition(first.job_id, JobState.IN_PROGRESS)

        runner_called = False

        def runner(_job):
            nonlocal runner_called
            runner_called = True
            return "should-not-run"

        outcome = self.worker.run_one(
            session_id="sess-1",
            role="ai-engineer",
            kind=KIND_OPEN,
            runner=runner,
        )
        # The runner must NOT execute when a duplicate is in flight —
        # exactly the regression M4 dedup is meant to prevent.
        self.assertFalse(runner_called)
        self.assertEqual(outcome.skipped_reason, "duplicate_in_flight")


class WorkerRetryablePathTests(_Fixture):
    def test_runner_exception_lands_failed_retryable(self) -> None:
        def boom(_job):
            raise RuntimeError("deliberation 503 throttle")

        with self.assertRaises(RuntimeError):
            self.worker.run_one(
                session_id="sess-1",
                role="ai-engineer",
                kind=KIND_TURN,
                runner=boom,
            )

        jobs = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(jobs), 1)
        failed = jobs[0]
        self.assertIsNone(failed.picked_by)
        self.assertIsNone(failed.picked_until)
        self.assertIn("RuntimeError", failed.result.get("error", ""))
        self.assertIn(
            "deliberation 503 throttle", failed.result.get("error", "")
        )


class DirectProcessJobTests(_Fixture):
    """``process_job`` exposed separately so M6's standalone
    per-role worker can pick its own row and run it without the
    producer-consumer combo of ``run_one``.
    """

    def test_process_job_drives_state_machine(self) -> None:
        seed, _ = self.worker.enqueue(
            session_id="sess-1", role="qa-engineer", kind=KIND_OPEN
        )
        picked = self.queue.pick(
            worker_id="external-worker",
            job_types=[JOB_TYPE_ROLE_TAKE],
            roles=["qa-engineer"],
        )
        assert picked is not None

        def runner(_job):
            return "ok"

        outcome = self.worker.process_job(picked, runner=runner)
        assert outcome.job is not None
        self.assertEqual(outcome.job.state, JobState.SAVED)


class SynthesisKindTests(_Fixture):
    """Synthesis is a separate ``kind`` so it doesn't share dedup
    with the chained ``turn`` kind for the same role (tech-lead).
    """

    def test_turn_and_synthesis_for_same_role_dedup_independently(self) -> None:
        turn, _ = self.worker.enqueue(
            session_id="sess-1", role="tech-lead", kind=KIND_TURN
        )
        synth, _ = self.worker.enqueue(
            session_id="sess-1", role="tech-lead", kind=KIND_SYNTHESIS
        )
        self.assertNotEqual(turn.job_id, synth.job_id)


if __name__ == "__main__":
    unittest.main()
