"""ci_retry_orchestrator — Round 3 of #73.

Pin the side-effect layer that turns CI status + retry log into a
``done`` / ``retry_ready`` / ``blocked`` decision and (for retry)
schedules the next coding_execute attempt.

Coverage:
  * success → done + completion hook fires (no requeue).
  * failure under budget → retry_ready + new coding_execute row.
  * failure over budget → blocked + completion hook fires (no requeue).
  * unknown CI → blocked.
  * pending → keep alive (no completion event).
  * GithubAppCheckRunFetcher swallows GitHub failures into
    ``conclusion=unknown`` (so the orchestrator escalates safely).
  * progress_post_fn failure does NOT corrupt the verdict.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, List, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.ci_retry_orchestrator import (
    CIRetryDecision,
    GithubAppCheckRunFetcher,
    SESSION_EXTRA_PROGRESS_KEY,
    orchestrate_ci_retry,
)
from yule_orchestrator.agents.job_queue.ci_status import (
    CI_FAILURE,
    CI_PENDING,
    CI_SUCCESS,
    CI_UNKNOWN,
    CIRetryPolicy,
    CIStatus,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)


def _coding_job(*, session_id: str = "sess-A") -> Mapping[str, Any]:
    return {
        "session_id": session_id,
        "user_request": "fix login",
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead"],
        "participant_roles": ["backend-engineer", "tech-lead"],
        "write_scope": ["services/auth/**"],
        "forbidden_scope": [".github/workflows/**"],
        "safety_rules": ["no force push"],
        "reason": "test",
        "status": "ready",
        "generated_prompt": "(prompt)",
        "created_at": "2026-05-08T00:00:00+00:00",
        "approved_at": "2026-05-08T01:00:00+00:00",
        "metadata": {
            "repo_full_name": "yule-studio/yule-studio-agent",
            "base_branch": "main",
            "issue_number": 99,
            "branch_hint": "agent/backend-engineer/issue-99-fix",
        },
    }


@dataclass
class _StaticFetcher:
    """Returns a canned :class:`CIStatus` regardless of arguments."""

    status: CIStatus

    def fetch(self, *, repo, pr_number, head_sha):
        return self.status


# Tracks `update_session` writes against an in-memory store keyed by
# session_id so the orchestrator's session.extra mutations are
# observable from the test.
class _SessionStore:
    def __init__(self) -> None:
        self.sessions: dict = {}

    def update(self, session, *, now):
        self.sessions[session.session_id] = session
        return session

    def get(self, sid):
        return self.sessions.get(sid)


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        self.worker = CodingExecutorWorker(queue=self.queue, heartbeats=self.heartbeats)
        self.store = _SessionStore()


# ---------------------------------------------------------------------------
# Verdict matrix
# ---------------------------------------------------------------------------


class SuccessPathTests(_Fixture):
    def test_ci_success_marks_done_and_does_not_requeue(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )
        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(pr_number=999, head_sha="aaa", conclusion=CI_SUCCESS)
            ),
            worker=self.worker,
            update_session_fn=self.store.update,
            env={},
        )
        self.assertEqual(decision.completion_status, "done")
        self.assertIsNone(decision.requeued_job_id)
        # Audit entry created via completion_hook.
        self.assertIsNotNone(decision.audit_entry_id)
        # No new coding_execute row queued.
        rows = [
            r
            for r in self.queue.list_for_session("sess-A")
            if r.job_type == JOB_TYPE_CODING_EXECUTE
        ]
        self.assertEqual(rows, [])


class FailureUnderBudgetTests(_Fixture):
    def test_first_failure_requeues_new_coding_execute(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )
        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(
                    pr_number=999,
                    head_sha="aaa",
                    conclusion=CI_FAILURE,
                    failing_runs=("test",),
                )
            ),
            worker=self.worker,
            policy=CIRetryPolicy(max_attempts=3),
            update_session_fn=self.store.update,
            env={},
        )
        self.assertEqual(decision.completion_status, "retry_ready")
        self.assertIsNotNone(decision.requeued_job_id)
        # New coding_execute row exists.
        rows = [
            r
            for r in self.queue.list_for_session("sess-A")
            if r.job_type == JOB_TYPE_CODING_EXECUTE
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, JobState.QUEUED)
        # branch_hint bumped with -attemptN suffix.
        self.assertIn("-attempt", rows[0].payload["branch_hint"])

    def test_progress_history_appended(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )
        orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(pr_number=999, head_sha="aaa", conclusion=CI_FAILURE)
            ),
            worker=self.worker,
            update_session_fn=self.store.update,
            env={},
        )
        persisted = self.store.get("sess-A")
        self.assertIsNotNone(persisted)
        history = persisted.extra.get(SESSION_EXTRA_PROGRESS_KEY)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["completion_status"], "retry_ready")
        self.assertEqual(history[0]["pr_number"], 999)


class FailureOverBudgetTests(_Fixture):
    def test_max_attempts_hit_blocks_and_does_not_requeue(self) -> None:
        # Pre-stamp a retry log at attempts=3 to simulate prior fails.
        prior = {
            "ci_retry_logs": {
                "999": {
                    "pr_number": 999,
                    "attempts": 3,
                    "last_attempt_at": "t",
                    "last_failure_reason": "test failed",
                    "head_sha_history": ["a1", "a2", "a3"],
                }
            }
        }
        session = _FakeSession(
            session_id="sess-A",
            extra={"coding_job": _coding_job(session_id="sess-A"), **prior},
        )
        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="bbb",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(pr_number=999, head_sha="bbb", conclusion=CI_FAILURE)
            ),
            worker=self.worker,
            policy=CIRetryPolicy(max_attempts=3),
            update_session_fn=self.store.update,
            env={},
        )
        self.assertEqual(decision.completion_status, "blocked")
        self.assertIsNone(decision.requeued_job_id)
        # No new coding_execute row queued.
        rows = [
            r
            for r in self.queue.list_for_session("sess-A")
            if r.job_type == JOB_TYPE_CODING_EXECUTE
        ]
        self.assertEqual(rows, [])
        # Completion hook fired — audit entry id present.
        self.assertIsNotNone(decision.audit_entry_id)


class UnknownAndPendingTests(_Fixture):
    def test_unknown_ci_blocks(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )
        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(pr_number=999, head_sha="aaa", conclusion=CI_UNKNOWN)
            ),
            worker=self.worker,
            update_session_fn=self.store.update,
            env={},
        )
        self.assertEqual(decision.completion_status, "blocked")

    def test_pending_keeps_alive_no_completion_hook(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )
        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(
                    pr_number=999, head_sha="aaa", conclusion=CI_PENDING, pending_runs=("test",)
                )
            ),
            worker=self.worker,
            update_session_fn=self.store.update,
            env={},
        )
        # Pending → derive_completion_status maps to retry_ready, but
        # decide_retry's verdict.should_retry stays False — no requeue.
        self.assertEqual(decision.completion_status, "retry_ready")
        self.assertIsNone(decision.requeued_job_id)
        # No completion hook for non-terminal pending.
        self.assertIsNone(decision.audit_entry_id)


# ---------------------------------------------------------------------------
# GithubAppCheckRunFetcher
# ---------------------------------------------------------------------------


class _FakeLive:
    def __init__(self, runs=None, raises: bool = False) -> None:
        self.runs = runs or []
        self.raises = raises
        self.calls: list = []

    def list_check_runs(self, *, repo, head_sha):
        self.calls.append((repo, head_sha))
        if self.raises:
            raise RuntimeError("github 502")
        return list(self.runs)


class GithubAppCheckRunFetcherTests(unittest.TestCase):
    def test_aggregates_into_ci_status(self) -> None:
        client = _FakeLive(
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "failure"},
            ]
        )
        fetcher = GithubAppCheckRunFetcher(live_client=client)
        status = fetcher.fetch(repo="o/r", pr_number=1, head_sha="abc")
        self.assertEqual(status.conclusion, CI_FAILURE)

    def test_github_failure_becomes_unknown(self) -> None:
        client = _FakeLive(raises=True)
        fetcher = GithubAppCheckRunFetcher(live_client=client)
        status = fetcher.fetch(repo="o/r", pr_number=1, head_sha="abc")
        self.assertEqual(status.conclusion, CI_UNKNOWN)


# ---------------------------------------------------------------------------
# progress_post_fn fault tolerance
# ---------------------------------------------------------------------------


class ProgressPosterFaultToleranceTests(_Fixture):
    def test_progress_post_failure_does_not_change_decision(self) -> None:
        session = _FakeSession(
            session_id="sess-A", extra={"coding_job": _coding_job(session_id="sess-A")}
        )

        def boom(**_kwargs):
            raise RuntimeError("discord 503")

        decision = orchestrate_ci_retry(
            session=session,
            pr_number=999,
            head_sha="aaa",
            repo="yule-studio/yule-studio-agent",
            fetcher=_StaticFetcher(
                CIStatus(pr_number=999, head_sha="aaa", conclusion=CI_SUCCESS)
            ),
            worker=self.worker,
            update_session_fn=self.store.update,
            env={},
            progress_post_fn=boom,
        )
        self.assertEqual(decision.completion_status, "done")
        # History still recorded.
        history = self.store.get("sess-A").extra.get(SESSION_EXTRA_PROGRESS_KEY)
        self.assertEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
