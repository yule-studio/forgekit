"""P1-L — coding_executor_worker 의 post-PR 후크 회귀.

worker 가 draft PR 을 성공적으로 열면, session.extra 에 다음이 stamp 되어야 함:

  * work_mode=autonomous_merge → pr_merge_stage = pr_merge_pending,
    pr_merge_repo / pr_merge_pr_number / head_sha / base_branch 등
    PR 메타 + audit 첫 줄에 action=autonomous_merge_continuation.
  * work_mode=approval_required → 동일 stage 지만 action=approval_required_continuation.
  * dry_run → stage 자체가 안 찍힘 (executor 가 아무 PR 도 안 만들었으니).

이 가드가 없으면 P1-K 까지 모두 통과한 뒤에도 draft PR 직후 멈춤 →
operator 가 다음 슬라이스 / 머지를 수동으로 trigger 해야 함.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    WorktreeContext,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_BASE_BRANCH,
    EXTRA_PR_MERGE_HEAD_SHA,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_PR_URL,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    PostPRAction,
    STAGE_PR_MERGE_PENDING,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_WORK_MODE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)


# ---------------------------------------------------------------------------
# Fake protocol seams — happy path
# ---------------------------------------------------------------------------


def _ctx_replace(ctx: WorktreeContext, **changes) -> WorktreeContext:
    from dataclasses import replace
    return replace(ctx, **changes)


class _FakeProvisioner:
    def provision(self, *, request, branch):
        return WorktreeContext(
            branch=branch,
            worktree_path=f"/tmp/{branch.replace('/', '_')}",
            base_commit_sha="basesha0",
        )


class _FakeEditor:
    def apply(self, *, request, context):
        return _ctx_replace(context, edited_files=("services/x.py",))


class _FakeTests:
    def run(self, *, request, context):
        return _ctx_replace(
            context, test_summary={"status": "ok", "passed": 3}
        )


class _FakeCommitter:
    def commit(self, *, request, context):
        return _ctx_replace(context, commit_sha="commitsha1234")


class _FakePusher:
    def push(self, *, request, context):
        return _ctx_replace(context, pushed=True)


class _FakePR:
    def open(self, *, request, context):
        return _ctx_replace(
            context,
            pr_number=42,
            pr_url="https://github.com/yule-studio/naver-search-clone/pull/42",
        )


def _request(*, session_id: str, dry_run: bool = False) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id=session_id,
        executor_role="backend-engineer",
        user_request="네이버 검색 풀스택 MVP 구현해줘",
        generated_prompt="(prompt)",
        write_scope=("services/**",),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name="yule-studio/naver-search-clone",
        issue_number=1,
        dry_run=dry_run,
        metadata={},
    )


def _seed_session(session_id: str, work_mode: str) -> None:
    now = datetime.now(tz=timezone.utc)
    save_session(
        WorkflowSession(
            session_id=session_id,
            prompt="네이버 검색 풀스택 MVP 구현해줘",
            task_type="coding_execute",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            executor_role="backend-engineer",
            extra={EXTRA_WORK_MODE: work_mode},
        )
    )


class _WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

    def _build_worker(self) -> CodingExecutorWorker:
        return CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_FakeProvisioner(),
            code_editor=_FakeEditor(),
            test_runner=_FakeTests(),
            committer=_FakeCommitter(),
            pusher=_FakePusher(),
            draft_pr_creator=_FakePR(),
        )


# ---------------------------------------------------------------------------
# 1. autonomous_merge worker 통과 시 stage stamp
# ---------------------------------------------------------------------------


class AutonomousMergeWorkerHookTests(_WorkerFixture):
    def test_autonomous_merge_session_gets_continuation_stage(self) -> None:
        session_id = "auto-merge-session-1"
        _seed_session(session_id, WORK_MODE_AUTONOMOUS)
        worker = self._build_worker()
        worker.enqueue(_request(session_id=session_id))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)
        self.assertEqual(outcome.pr_number, 42)

        session = load_session(session_id)
        self.assertIsNotNone(session)
        extra = dict(session.extra)
        self.assertEqual(extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_PENDING)
        self.assertEqual(extra[EXTRA_PR_MERGE_PR_NUMBER], 42)
        self.assertEqual(
            extra[EXTRA_PR_MERGE_REPO], "yule-studio/naver-search-clone"
        )
        self.assertEqual(extra[EXTRA_PR_MERGE_HEAD_SHA], "commitsha1234")
        self.assertEqual(extra[EXTRA_PR_MERGE_BASE_BRANCH], "main")
        self.assertEqual(
            extra[EXTRA_PR_MERGE_PR_URL],
            "https://github.com/yule-studio/naver-search-clone/pull/42",
        )
        audit = list(extra.get(EXTRA_PR_MERGE_AUDIT) or ())
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["stage"], STAGE_PR_MERGE_PENDING)
        self.assertEqual(
            audit[0]["action"], PostPRAction.AUTONOMOUS_MERGE.value
        )


# ---------------------------------------------------------------------------
# 2. approval_required worker 통과 시 stage stamp
# ---------------------------------------------------------------------------


class ApprovalRequiredWorkerHookTests(_WorkerFixture):
    def test_approval_required_session_gets_continuation_stage(self) -> None:
        session_id = "approval-session-2"
        _seed_session(session_id, WORK_MODE_APPROVAL)
        worker = self._build_worker()
        worker.enqueue(_request(session_id=session_id))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)

        session = load_session(session_id)
        extra = dict(session.extra)
        self.assertEqual(extra[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_PENDING)
        audit = list(extra.get(EXTRA_PR_MERGE_AUDIT) or ())
        self.assertEqual(
            audit[0]["action"], PostPRAction.APPROVAL_REQUIRED.value
        )


# ---------------------------------------------------------------------------
# 3. dry_run 경로는 stage stamp 안 함
# ---------------------------------------------------------------------------


class DryRunSkipsHookTests(_WorkerFixture):
    def test_dry_run_does_not_stamp_pr_merge_stage(self) -> None:
        session_id = "dry-run-session-3"
        _seed_session(session_id, WORK_MODE_AUTONOMOUS)
        # dry_run path 는 protocol stubs 안 부르고 SAVED + REASON_DRY_RUN
        # 으로 빠짐 — PR 자체가 안 만들어졌으니 continuation 도 없어야 함.
        worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        worker.enqueue(_request(session_id=session_id, dry_run=True))
        outcome = worker.run_one(worker_id="t")
        self.assertEqual(outcome.terminal_state, JobState.SAVED.value)

        session = load_session(session_id)
        extra = dict(session.extra)
        # work_mode 는 그대로 있어야 함
        self.assertEqual(extra[EXTRA_WORK_MODE], WORK_MODE_AUTONOMOUS)
        # 하지만 pr_merge_stage 는 절대 안 찍힘
        self.assertNotIn(EXTRA_PR_MERGE_STAGE, extra)
        self.assertNotIn(EXTRA_PR_MERGE_AUDIT, extra)


if __name__ == "__main__":
    unittest.main()
