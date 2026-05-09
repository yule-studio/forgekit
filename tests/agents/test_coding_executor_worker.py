"""coding_executor_worker — Phase 1 of #73.

Pin the contract:
  * `coding_execute` job type roundtrips through the queue.
  * Protocol seams (`WorktreeProvisioner` etc.) are individually
    overridable.
  * Hard rails: protected branch / force push / dry_run.
  * Default `_NotImplementedStep` lands jobs in FAILED_TERMINAL with
    a ``executor_not_wired_yet`` reason — proving the executor never
    smoke-runs against production by accident.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteOutcome,
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
    REASON_DRY_RUN,
    REASON_NOT_IMPLEMENTED,
    REASON_PROTECTED_BRANCH,
    REASON_TEST_FAILED,
    WorktreeContext,
    is_protected_branch,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


def _request(**overrides) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-coding-1",
        "executor_role": "backend-engineer",
        "user_request": "users 401 회복",
        "generated_prompt": "(prompt)",
        "write_scope": ("services/auth/**",),
        "forbidden_scope": (".github/workflows/**",),
        "safety_rules": ("no force push",),
        "base_branch": "main",
        "branch_hint": "agent/backend-engineer/issue-99-fix",
        "repo_full_name": "yule-studio/yule-studio-agent",
        "issue_number": 99,
        "dry_run": True,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


# ---------------------------------------------------------------------------
# Hard rail — protected branch
# ---------------------------------------------------------------------------


class ProtectedBranchTests(unittest.TestCase):
    def test_main_master_dev_release_blocked(self) -> None:
        for name in ("main", "MAIN", "master", "dev", "develop", "prod", "release", "release/2026-q2", "hotfix/x"):
            with self.subTest(name=name):
                self.assertTrue(is_protected_branch(name), name)

    def test_feature_branches_pass(self) -> None:
        for name in (
            "feature/foo",
            "agent/backend-engineer/issue-99-fix",
            "fix/users-401",
            "agent/qa-engineer/regression-2026-05",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_protected_branch(name), name)


# ---------------------------------------------------------------------------
# Request payload round-trip
# ---------------------------------------------------------------------------


class RequestRoundTripTests(unittest.TestCase):
    def test_payload_round_trip(self) -> None:
        req = _request()
        payload = req.to_payload()
        rehydrated = CodingExecuteRequest.from_payload(payload)
        self.assertEqual(rehydrated.session_id, req.session_id)
        self.assertEqual(rehydrated.write_scope, req.write_scope)
        self.assertEqual(rehydrated.forbidden_scope, req.forbidden_scope)
        self.assertEqual(rehydrated.dry_run, req.dry_run)
        self.assertEqual(rehydrated.issue_number, 99)


# ---------------------------------------------------------------------------
# Worker — dry-run + protected-branch + not-implemented
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)
        self.worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )


class EnqueueDedupTests(_Fixture):
    def test_first_enqueue_creates_job(self) -> None:
        job, created = self.worker.enqueue(_request())
        self.assertTrue(created)
        self.assertEqual(job.job_type, JOB_TYPE_CODING_EXECUTE)
        self.assertEqual(job.state, JobState.QUEUED)

    def test_dedup_same_session_role_branch(self) -> None:
        first, _ = self.worker.enqueue(_request())
        second, created = self.worker.enqueue(_request())
        self.assertFalse(created)
        self.assertEqual(first.job_id, second.job_id)

    def test_different_role_is_separate_job(self) -> None:
        first, _ = self.worker.enqueue(_request())
        second, created = self.worker.enqueue(_request(executor_role="frontend-engineer"))
        self.assertTrue(created)
        self.assertNotEqual(first.job_id, second.job_id)


class ProtectedBranchWorkerTests(_Fixture):
    def test_protected_branch_lands_terminal(self) -> None:
        # branch_hint = main triggers hard rail.
        req = _request(branch_hint="main", dry_run=False)
        job, _ = self.worker.enqueue(req)
        outcome = self.worker.run_one(worker_id="test")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_TERMINAL.value)
        self.assertIn(REASON_PROTECTED_BRANCH, outcome.failure_reason or "")


class DryRunPathTests(_Fixture):
    def test_dry_run_lands_saved_without_protocols(self) -> None:
        # Default Protocol stubs raise — but dry_run never invokes them.
        req = _request(dry_run=True)
        self.worker.enqueue(req)
        outcome = self.worker.run_one(worker_id="test")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)
        self.assertIsNone(outcome.failure_reason)
        self.assertEqual(outcome.test_summary, {"dry_run": True})


class NotImplementedStepTests(_Fixture):
    def test_live_run_without_protocols_lands_terminal(self) -> None:
        # No injected Protocols + dry_run=False → first Protocol invocation
        # raises CodingExecutorNotImplementedError, worker maps to terminal
        # with executor_not_wired_yet reason.
        req = _request(dry_run=False)
        self.worker.enqueue(req)
        outcome = self.worker.run_one(worker_id="test")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_TERMINAL.value)
        self.assertIn(REASON_NOT_IMPLEMENTED, outcome.failure_reason or "")


# ---------------------------------------------------------------------------
# Worker — fake injection for end-to-end happy path
# ---------------------------------------------------------------------------


class _FakeProvisioner:
    def provision(self, *, request, branch):
        return WorktreeContext(
            branch=branch,
            worktree_path=f"/tmp/{branch.replace('/', '_')}",
            base_commit_sha="deadbeef",
        )


class _FakeEditor:
    def apply(self, *, request, context):
        return _replace(context, edited_files=("services/auth/handlers.py",))


class _FakeTests:
    def __init__(self, status: str = "ok") -> None:
        self.status = status

    def run(self, *, request, context):
        return _replace(context, test_summary={"status": self.status, "passed": 5})


class _FakeCommitter:
    def commit(self, *, request, context):
        return _replace(context, commit_sha="abc123def")


class _FakePusher:
    def push(self, *, request, context):
        return _replace(context, pushed=True)


class _FakePR:
    def open(self, *, request, context):
        return _replace(context, pr_number=99, pr_url="https://github.com/x/y/pull/99")


def _replace(ctx: WorktreeContext, **changes) -> WorktreeContext:
    """Helper because WorktreeContext is frozen."""

    from dataclasses import replace
    return replace(ctx, **changes)


class HappyPathFakeTests(_Fixture):
    def test_all_seven_steps_with_fake_protocols(self) -> None:
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests("ok"),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_request(dry_run=False))
        outcome = worker.run_one(worker_id="test")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)
        self.assertEqual(outcome.commit_sha, "abc123def")
        self.assertEqual(outcome.pr_number, 99)
        self.assertEqual(outcome.pr_url, "https://github.com/x/y/pull/99")

    def test_test_failure_lands_retryable(self) -> None:
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests("failed"),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_request(dry_run=False))
        outcome = worker.run_one(worker_id="test")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_RETRYABLE.value)
        self.assertEqual(outcome.failure_reason, REASON_TEST_FAILED)


if __name__ == "__main__":
    unittest.main()
