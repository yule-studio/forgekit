"""coding executor ↔ runtime_policy 통합 회귀 — P0-T caller wiring.

본 test 가 통과한다 = runtime_policy 의 hard rail 이 실제 executor 행동에
연결돼 있다는 뜻.

검사:
  1. branch 가 protected qualified ref (`refs/heads/main`) 라도 거부
  2. branch 형식 위반 (대문자) 거부
  3. progress marker stamp:
     - coding_in_progress (process_job 진입 직후)
     - coding_blocked (fail 시)
     - draft_pr_opened (happy path 성공 + dry_run 성공)
  4. _draft_pr_body 가 PR_REQUIRED_SECTIONS + audit block 모두 만족
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.governance.runtime_policy import (
    PR_REQUIRED_SECTIONS,
    validate_pr_body,
)
from yule_engineering.agents.job_queue.coding_executor_live import (
    _draft_pr_body,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    REASON_BRANCH_POLICY_VIOLATION,
    REASON_DRY_RUN,
    REASON_PROTECTED_BRANCH,
    REASON_TEST_FAILED,
    WorktreeContext,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.job_queue.work_order_coding_continuation import (
    PROGRESS_CODING_BLOCKED,
    PROGRESS_CODING_IN_PROGRESS,
    PROGRESS_DRAFT_PR_OPENED,
    SESSION_EXTRA_PROGRESS_KEY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(**overrides) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-policy-1",
        "executor_role": "backend-engineer",
        "user_request": "users 401",
        "generated_prompt": "(prompt)",
        "write_scope": ("services/auth/**",),
        "forbidden_scope": (".github/workflows/**",),
        "safety_rules": ("no force push",),
        "base_branch": "main",
        "branch_hint": "agent/backend-engineer/issue-77-coding-execute",
        "repo_full_name": "yule-studio/naver-search-clone",
        "issue_number": 77,
        "dry_run": True,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


def _replace(ctx: WorktreeContext, **changes) -> WorktreeContext:
    return replace(ctx, **changes)


@dataclass
class _SessionFake:
    session_id: str
    extra: Dict[str, Any] = field(default_factory=dict)


# fake session store — patched via the workflow_state seam used by
# `_stamp_progress`. We monkey-patch load_session / update_session so
# the worker's progress stamper goes through us instead of SQLite.
class _SessionRegistry:
    instance: "Optional[_SessionRegistry]" = None

    def __init__(self) -> None:
        self.sessions: Dict[str, _SessionFake] = {}

    def make(self, session_id: str) -> _SessionFake:
        s = _SessionFake(session_id=session_id, extra={})
        self.sessions[session_id] = s
        return s


def _patch_workflow_state(registry: _SessionRegistry):
    """Monkey-patch workflow_state.load_session / update_session.

    Used by `CodingExecutorWorker._stamp_progress` to read/write the
    progress markers on session.extra.
    """

    from yule_engineering.agents import workflow_state as _ws

    def _load(session_id: str):
        return registry.sessions.get(session_id)

    def _update(session, *, now=None):
        registry.sessions[session.session_id] = session
        return session

    original_load = _ws.load_session
    original_update = _ws.update_session
    _ws.load_session = _load
    _ws.update_session = _update
    return original_load, original_update


# ---------------------------------------------------------------------------
# Fake protocols
# ---------------------------------------------------------------------------


class _FakeProvisioner:
    def provision(self, *, request, branch):
        return WorktreeContext(branch=branch, worktree_path="/tmp/x", base_commit_sha="dead")


class _FakeEditor:
    def apply(self, *, request, context):
        return _replace(context, edited_files=("services/auth/x.py",))


class _FakeTests:
    def __init__(self, status="ok") -> None:
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


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue = JobQueue(db_path=Path(self._tmp.name) / "q.sqlite3")
        self.heartbeats = HeartbeatStore(db_path=Path(self._tmp.name) / "q.sqlite3")
        self.registry = _SessionRegistry()
        self._origs = _patch_workflow_state(self.registry)
        self.addCleanup(self._restore_workflow_state)

    def _restore_workflow_state(self) -> None:
        from yule_engineering.agents import workflow_state as _ws

        _ws.load_session = self._origs[0]
        _ws.update_session = self._origs[1]


# ---------------------------------------------------------------------------
# Branch policy integration
# ---------------------------------------------------------------------------


class BranchPolicyIntegrationTests(_Fixture):
    def test_qualified_protected_ref_blocked_by_policy(self) -> None:
        """runtime_policy 가 ``refs/heads/main`` 도 거부 — 기존
        is_protected_branch 의 더 좁은 set 을 넘어선 검사."""

        worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        # branch hint 가 qualified — local is_protected_branch 는 막지만,
        # 직접 segment 가 protected 라 동일 결과. 본 케이스는 protected 경로 핀.
        worker.enqueue(_request(branch_hint="refs/heads/main", dry_run=False))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_TERMINAL.value)
        # 어느 hard rail 이 잡았든 protected 또는 policy violation
        self.assertTrue(
            REASON_PROTECTED_BRANCH in (outcome.failure_reason or "")
            or REASON_BRANCH_POLICY_VIOLATION in (outcome.failure_reason or "")
        )

    def test_invalid_branch_chars_blocked_by_policy(self) -> None:
        worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        # 대문자 + 공백 — local is_protected_branch 는 못 막지만 runtime_policy
        # 의 validate_branch_name 이 invalid_branch_chars 로 거부
        worker.enqueue(_request(branch_hint="Feature/With Space", dry_run=False))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_TERMINAL.value)
        self.assertIn(REASON_BRANCH_POLICY_VIOLATION, outcome.failure_reason or "")


# ---------------------------------------------------------------------------
# Progress marker stamping
# ---------------------------------------------------------------------------


class ProgressMarkerTests(_Fixture):
    def test_dry_run_stamps_in_progress_and_pr_opened(self) -> None:
        self.registry.make("sess-policy-1")
        worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        worker.enqueue(_request(dry_run=True))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)
        progress = self.registry.sessions["sess-policy-1"].extra.get(
            SESSION_EXTRA_PROGRESS_KEY
        )
        self.assertIsNotNone(progress)
        assert isinstance(progress, Mapping)
        self.assertIn(PROGRESS_CODING_IN_PROGRESS, progress)
        self.assertIn(PROGRESS_DRAFT_PR_OPENED, progress)
        # dry_run 표시
        self.assertTrue(
            progress[PROGRESS_DRAFT_PR_OPENED]["detail"].get("dry_run")
        )

    def test_protected_branch_stamps_coding_blocked(self) -> None:
        self.registry.make("sess-policy-1")
        worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        worker.enqueue(_request(branch_hint="main", dry_run=False))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_TERMINAL.value)
        progress = self.registry.sessions["sess-policy-1"].extra.get(
            SESSION_EXTRA_PROGRESS_KEY
        )
        self.assertIsNotNone(progress)
        assert isinstance(progress, Mapping)
        self.assertIn(PROGRESS_CODING_BLOCKED, progress)
        self.assertEqual(
            progress[PROGRESS_CODING_BLOCKED]["detail"]["reason"],
            REASON_PROTECTED_BRANCH,
        )

    def test_test_failure_stamps_coding_blocked(self) -> None:
        self.registry.make("sess-policy-1")
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests(status="failed"),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_request(dry_run=False))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.FAILED_RETRYABLE.value)
        self.assertEqual(outcome.failure_reason, REASON_TEST_FAILED)
        progress = self.registry.sessions["sess-policy-1"].extra.get(
            SESSION_EXTRA_PROGRESS_KEY
        )
        assert isinstance(progress, Mapping)
        self.assertEqual(
            progress[PROGRESS_CODING_BLOCKED]["detail"]["reason"],
            REASON_TEST_FAILED,
        )

    def test_happy_path_stamps_draft_pr_opened_with_pr_number(self) -> None:
        self.registry.make("sess-policy-1")
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )
        worker.enqueue(_request(dry_run=False))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)
        progress = self.registry.sessions["sess-policy-1"].extra.get(
            SESSION_EXTRA_PROGRESS_KEY
        )
        assert isinstance(progress, Mapping)
        self.assertEqual(
            progress[PROGRESS_DRAFT_PR_OPENED]["detail"]["pr_number"], 99
        )
        self.assertEqual(
            progress[PROGRESS_DRAFT_PR_OPENED]["detail"]["branch"],
            "agent/backend-engineer/issue-77-coding-execute",
        )


# ---------------------------------------------------------------------------
# Draft PR body validation
# ---------------------------------------------------------------------------


class DraftPRBodyValidationTests(unittest.TestCase):
    def test_draft_pr_body_satisfies_runtime_policy(self) -> None:
        request = _request(dry_run=False)
        ctx = WorktreeContext(
            branch="agent/backend-engineer/issue-77-coding-execute",
            commit_sha="abc1234567",
            test_summary={"status": "ok", "passed": 5},
        )
        body = _draft_pr_body(request, ctx)
        result = validate_pr_body(body)
        self.assertTrue(
            result.ok,
            f"draft PR body 가 runtime_policy 를 통과하지 못함: "
            f"missing={result.missing_sections} warnings={result.warnings}",
        )
        # 모든 5 섹션 alternative 매칭
        self.assertEqual(result.missing_sections, ())
        self.assertTrue(result.audit_block_present)

    def test_draft_pr_body_includes_issue_anchor(self) -> None:
        request = _request(issue_number=42)
        ctx = WorktreeContext(branch="agent/backend-engineer/issue-42")
        body = _draft_pr_body(request, ctx)
        self.assertIn("close #42", body)


if __name__ == "__main__":
    unittest.main()
