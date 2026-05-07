"""SQLite-backed JobQueue — enqueue / pick / transition / lease reaper.

Each test uses an isolated SQLite file so concurrent test runs don't
share the queue. Pin the contract: ``pick`` is atomic, expired leases
get reaped, dependencies block fanout, retryable jobs requeue with
backoff and escalate to terminal at max_attempts.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import (
    DEFAULT_LEASE_SECONDS,
    JobQueue,
    JobQueueError,
)


class _QueueFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Force the queue store off the shared cache file so tests
        # never touch the developer's local cache. We pass db_path
        # explicitly instead of mutating YULE_CACHE_DB_PATH so unrelated
        # tests sharing the same process don't see env mutation.
        self._db_path = Path(self._tmpdir.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db_path)


class EnqueueTests(_QueueFixture):
    def test_default_state_is_queued(self) -> None:
        job = self.queue.enqueue(
            session_id="sess-1",
            job_type="role_take",
            role="ai-engineer",
            payload={"prompt": "hello"},
        )
        self.assertEqual(job.state, JobState.QUEUED)
        self.assertEqual(job.role, "ai-engineer")
        self.assertEqual(job.attempt, 0)
        self.assertEqual(job.payload["prompt"], "hello")

    def test_after_jobs_starts_in_waiting_for_role(self) -> None:
        parent = self.queue.enqueue(session_id="sess-1", job_type="frame")
        child = self.queue.enqueue(
            session_id="sess-1",
            job_type="role_take",
            role="backend-engineer",
            after_jobs=[parent.job_id],
        )
        # Children with unsatisfied parents stay out of the queued
        # pool until the parent reaches a terminal success.
        self.assertEqual(child.state, JobState.WAITING_FOR_ROLE)

    def test_max_attempts_validates(self) -> None:
        with self.assertRaises(JobQueueError):
            self.queue.enqueue(
                session_id="sess-1",
                job_type="role_take",
                max_attempts=0,
            )


class PickTests(_QueueFixture):
    def test_pick_assigns_lease_and_state(self) -> None:
        self.queue.enqueue(
            session_id="sess-1", job_type="role_take", now=1000.0
        )
        picked = self.queue.pick(worker_id="worker-A", now=1000.0)
        self.assertIsNotNone(picked)
        assert picked is not None  # mypy
        self.assertEqual(picked.state, JobState.ASSIGNED)
        self.assertEqual(picked.picked_by, "worker-A")
        self.assertEqual(picked.picked_until, 1000.0 + DEFAULT_LEASE_SECONDS)

    def test_concurrent_pick_does_not_double_assign(self) -> None:
        # Only one job, two workers — the second pick must return None.
        self.queue.enqueue(session_id="sess-1", job_type="role_take")
        first = self.queue.pick(worker_id="worker-A")
        second = self.queue.pick(worker_id="worker-B")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_role_filter_isolates_role_workers(self) -> None:
        # backend worker must not see ai-engineer jobs.
        self.queue.enqueue(
            session_id="sess-1", job_type="role_take", role="ai-engineer"
        )
        backend_pick = self.queue.pick(
            worker_id="backend-worker",
            roles=["backend-engineer"],
        )
        self.assertIsNone(backend_pick)

    def test_dependency_blocks_pick_until_parent_saved(self) -> None:
        # Move parent into in_progress immediately so the only
        # *queued* candidate is the (dependency-blocked) child.
        parent = self.queue.enqueue(session_id="sess-1", job_type="frame")
        parent_picked = self.queue.pick(
            worker_id="parent-worker", job_types=["frame"]
        )
        assert parent_picked is not None
        self.queue.transition(parent_picked.job_id, JobState.IN_PROGRESS)

        child = self.queue.enqueue(
            session_id="sess-1",
            job_type="role_take",
            role="backend-engineer",
            after_jobs=[parent.job_id],
        )
        # WAITING_FOR_ROLE → QUEUED to make it nominally eligible.
        self.queue.transition(child.job_id, JobState.QUEUED)

        # Parent isn't saved yet → child stays blocked even though
        # it's queued.
        self.assertIsNone(
            self.queue.pick(
                worker_id="role-worker",
                job_types=["role_take"],
                roles=["backend-engineer"],
            )
        )

        # Drive the parent through to SAVED so the child unblocks.
        self.queue.transition(parent_picked.job_id, JobState.SAVED)

        unblocked = self.queue.pick(
            worker_id="role-worker",
            job_types=["role_take"],
            roles=["backend-engineer"],
        )
        self.assertIsNotNone(unblocked)


class TransitionTests(_QueueFixture):
    def test_invalid_transition_raises(self) -> None:
        job = self.queue.enqueue(session_id="sess-1", job_type="role_take")
        # QUEUED → SAVED is not a legal jump; must reject.
        with self.assertRaises(ValueError):
            self.queue.transition(job.job_id, JobState.SAVED)

    def test_result_merges_on_transition(self) -> None:
        job = self.queue.enqueue(session_id="sess-1", job_type="role_take")
        picked = self.queue.pick(worker_id="worker-A")
        assert picked is not None
        self.queue.transition(
            picked.job_id, JobState.IN_PROGRESS, result={"step": "started"}
        )
        updated = self.queue.transition(
            picked.job_id,
            JobState.READY_FOR_OBSIDIAN,
            result={"source_count": 3},
        )
        # Both keys must persist — transition merges, doesn't overwrite.
        self.assertEqual(updated.result.get("step"), "started")
        self.assertEqual(updated.result.get("source_count"), 3)


class FanoutTests(_QueueFixture):
    def test_enqueue_fanout_creates_one_job_per_role(self) -> None:
        parent = self.queue.enqueue(session_id="sess-1", job_type="frame")
        children = self.queue.enqueue_fanout(
            session_id="sess-1",
            job_type="role_take",
            roles=("backend-engineer", "qa-engineer", "devops-engineer"),
            after_jobs=[parent.job_id],
        )
        self.assertEqual(len(children), 3)
        roles = {c.role for c in children}
        self.assertEqual(
            roles,
            {"backend-engineer", "qa-engineer", "devops-engineer"},
        )
        # All children share the same parent dependency.
        for child in children:
            self.assertEqual(child.state, JobState.WAITING_FOR_ROLE)


class LeaseReaperTests(_QueueFixture):
    def test_expired_lease_moves_to_failed_retryable(self) -> None:
        self.queue.enqueue(
            session_id="sess-1", job_type="role_take", now=1000.0
        )
        picked = self.queue.pick(worker_id="worker-A", now=1000.0)
        assert picked is not None
        # Reap when the lease has clearly expired (well past picked_until).
        moved = self.queue.reap_expired_leases(now=picked.picked_until + 1)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].state, JobState.FAILED_RETRYABLE)
        # Lease must be cleared so the next pick doesn't think the
        # original worker is still on it.
        self.assertIsNone(moved[0].picked_by)
        self.assertIsNone(moved[0].picked_until)

    def test_active_lease_is_not_reaped(self) -> None:
        self.queue.enqueue(
            session_id="sess-1", job_type="role_take", now=1000.0
        )
        self.queue.pick(worker_id="worker-A", now=1000.0)
        # Now is BEFORE picked_until — no reaping.
        self.assertEqual(self.queue.reap_expired_leases(now=1010.0), ())


class RequeueRetryableTests(_QueueFixture):
    def test_requeue_increments_attempt_and_applies_backoff(self) -> None:
        job = self.queue.enqueue(
            session_id="sess-1",
            job_type="role_take",
            max_attempts=3,
            now=1000.0,
        )
        picked = self.queue.pick(worker_id="worker-A", now=1000.0)
        assert picked is not None
        self.queue.transition(picked.job_id, JobState.IN_PROGRESS)
        self.queue.transition(picked.job_id, JobState.FAILED_RETRYABLE)
        requeued = self.queue.requeue_retryable(
            picked.job_id, backoff_seconds=10.0, now=2000.0
        )
        self.assertEqual(requeued.state, JobState.QUEUED)
        self.assertEqual(requeued.attempt, 1)
        # Backoff prevents the requeued job from being picked
        # immediately at now=2000.
        self.assertEqual(requeued.available_at, 2010.0)
        self.assertIsNone(self.queue.pick(worker_id="worker-A", now=2005.0))
        # Once the backoff window passes, the job is eligible again.
        self.assertIsNotNone(self.queue.pick(worker_id="worker-A", now=2010.0))

    def test_max_attempts_escalates_to_failed_terminal(self) -> None:
        job = self.queue.enqueue(
            session_id="sess-1", job_type="role_take", max_attempts=2
        )
        # Drive through one retry cycle: attempt 0 → 1.
        picked = self.queue.pick(worker_id="worker-A")
        assert picked is not None
        self.queue.transition(picked.job_id, JobState.IN_PROGRESS)
        self.queue.transition(picked.job_id, JobState.FAILED_RETRYABLE)
        self.queue.requeue_retryable(picked.job_id, backoff_seconds=0)
        # Second attempt — at this point requeue must escalate because
        # next_attempt (=2) >= max_attempts (=2).
        picked2 = self.queue.pick(worker_id="worker-A")
        assert picked2 is not None
        self.queue.transition(picked2.job_id, JobState.IN_PROGRESS)
        self.queue.transition(picked2.job_id, JobState.FAILED_RETRYABLE)
        terminal = self.queue.requeue_retryable(picked2.job_id)
        self.assertEqual(terminal.state, JobState.FAILED_TERMINAL)


class ListAndGetTests(_QueueFixture):
    def test_list_for_session_filters_states(self) -> None:
        a = self.queue.enqueue(session_id="sess-A", job_type="role_take")
        b = self.queue.enqueue(session_id="sess-A", job_type="role_take")
        self.queue.enqueue(session_id="sess-B", job_type="role_take")
        # Drive one A-job through to SAVED.
        picked = self.queue.pick(
            worker_id="w", job_types=["role_take"], roles=()
        )
        assert picked is not None
        self.queue.transition(picked.job_id, JobState.IN_PROGRESS)
        self.queue.transition(picked.job_id, JobState.SAVED)

        all_a = self.queue.list_for_session("sess-A")
        self.assertEqual(len(all_a), 2)
        saved_a = self.queue.list_for_session(
            "sess-A", states=[JobState.SAVED]
        )
        self.assertEqual(len(saved_a), 1)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.queue.get("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
