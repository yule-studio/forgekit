"""GitHubWorkOrderWorker — issue auto-create 종단 회귀 핀.

contract:
  - existing_issue_number 가 있으면 issue 생성 안 함, session.extra 에
    existing_anchor 로 stamp + 큐 SAVED.
  - issue_auto_create_plan 이 있으면 writer.create_issue 호출, 결과
    issue number/url 을 session.extra 에 stamp.
  - writer 가 dry_run → succeeded=False → ``created_via='dry_run_plan'``
    + SAVED (요청은 실패가 아니라 plan-only 결정).
  - repo 가 비어있으면 failed_retryable + SKIPPED_NO_REPO.
  - plan 도 existing 도 없으면 failed_retryable + SKIPPED_MISSING_PLAN.
  - run_one 이 queue 에서 한 건만 drain, 다른 session 의 job 은 lease
    되돌림.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.audit import (
    OUTCOME_DRY_RUN,
    OUTCOME_OK,
)
from yule_orchestrator.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    JOB_TYPE_GITHUB_WORK_ORDER,
    dispatch_github_work_order,
)
from yule_orchestrator.agents.job_queue.github_work_order_executor import (
    CREATED_VIA_AUTO_CREATE,
    CREATED_VIA_DRY_RUN,
    CREATED_VIA_EXISTING_ANCHOR,
    GitHubWorkOrderWorker,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
    SKIPPED_MISSING_PLAN,
    SKIPPED_NO_REPO,
    SKIPPED_NO_WRITER,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _SessionFake:
    session_id: str
    extra: Dict[str, Any] = field(default_factory=dict)


class _FakeSessionStore:
    def __init__(self) -> None:
        self.sessions: Dict[str, _SessionFake] = {}

    def add(self, session_id: str) -> _SessionFake:
        s = _SessionFake(session_id=session_id, extra={})
        self.sessions[session_id] = s
        return s

    def load(self, session_id: str):
        return self.sessions.get(session_id)

    def update(self, session, new_extra: Mapping[str, Any]):
        session.extra = dict(new_extra)
        self.sessions[session.session_id] = session
        return session


@dataclass
class _WriterResult:
    ok: bool
    outcome: str
    body: Mapping[str, Any] = field(default_factory=dict)
    succeeded: bool = False


class _RecordingWriter:
    def __init__(self, *, response: Optional[Mapping[str, Any]] = None, succeeded: bool = True) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._response = response or {
            "number": 77,
            "html_url": "https://github.com/yule-studio/naver-search-clone/issues/77",
            "url": "https://api.github.com/.../issues/77",
        }
        self._succeeded = succeeded

    def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels=(),
        assignees=(),
        autonomy_level: str = "L2",
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ):
        self.calls.append(
            (
                "create_issue",
                {
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "labels": tuple(labels),
                    "assignees": tuple(assignees),
                    "autonomy_level": autonomy_level,
                    "session_id": session_id,
                    "decision_id": decision_id,
                },
            )
        )
        if self._succeeded:
            return _WriterResult(
                ok=True,
                outcome=OUTCOME_OK,
                body=dict(self._response),
                succeeded=True,
            )
        return _WriterResult(
            ok=True,
            outcome=OUTCOME_DRY_RUN,
            body={},
            succeeded=False,
        )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue = JobQueue(db_path=Path(self._tmp.name) / "q.sqlite3")
        self.sessions = _FakeSessionStore()
        self.writer = _RecordingWriter()

        def _writer_factory(_wo):
            return self.writer, "L2"

        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=_writer_factory,
            load_session_fn=lambda sid: self.sessions.load(sid),
            update_session_fn=lambda s, e: self.sessions.update(s, e),
        )

    def _enqueue(self, wo: GitHubWorkOrder):
        outcome = dispatch_github_work_order(self.queue, wo)
        self.assertIsNotNone(outcome.job)
        return outcome.job

    def _sample_plan(self, audit_reason: str = "template_used") -> Mapping[str, Any]:
        return {
            "title": "[Feat] 회원가입/검색 구현",
            "body": "## 어떤 기능인가요?\n> 본문\n",
            "labels": ["✨ Feature", "📃 Docs"],
            "assignees": [],
            "template_path": ".github/ISSUE_TEMPLATE/feature.md",
            "confidence": "high",
            "audit_reason": audit_reason,
            "needs_operator_decision": False,
            "template_score": 2,
        }


# ---------------------------------------------------------------------------
# Auto-create branch
# ---------------------------------------------------------------------------


class AutoCreateBranchTests(_Fixture):
    def test_creates_issue_and_stamps_session(self) -> None:
        self.sessions.add("sess-1")
        wo = GitHubWorkOrder(
            proposal_id="p1",
            session_id="sess-1",
            approval_id="approval-1",
            approved_by="masterway",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="full-stack 구현",
            repo="yule-studio/naver-search-clone",
            dry_run=False,
            issue_auto_create_plan=self._sample_plan(),
        )
        job = self._enqueue(wo)
        outcome = self.worker.run_one(now=None)
        assert outcome is not None
        self.assertEqual(outcome.created_via, CREATED_VIA_AUTO_CREATE)
        self.assertEqual(outcome.issue_number, 77)
        self.assertTrue(outcome.issue_url and "issues/77" in outcome.issue_url)
        # writer called with plan content
        self.assertEqual(len(self.writer.calls), 1)
        _, call_kwargs = self.writer.calls[0]
        self.assertTrue(call_kwargs["title"].startswith("[Feat]"))
        self.assertIn("✨ Feature", call_kwargs["labels"])
        # session extra anchor
        anchor = self.sessions.sessions["sess-1"].extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]
        self.assertEqual(anchor["issue_number"], 77)
        self.assertEqual(anchor["created_via"], CREATED_VIA_AUTO_CREATE)
        self.assertEqual(anchor["approval_id"], "approval-1")
        self.assertEqual(anchor["repo"], "yule-studio/naver-search-clone")
        # queue row SAVED
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.SAVED)
        self.assertEqual(refreshed.result.get("issue_number"), 77)
        self.assertEqual(refreshed.result.get("created_via"), CREATED_VIA_AUTO_CREATE)

    def test_dry_run_writer_falls_back_to_plan_only(self) -> None:
        self.writer = _RecordingWriter(succeeded=False)

        def _writer_factory(_wo):
            return self.writer, "L2"

        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=_writer_factory,
            load_session_fn=lambda sid: self.sessions.load(sid),
            update_session_fn=lambda s, e: self.sessions.update(s, e),
        )
        self.sessions.add("sess-dry")
        wo = GitHubWorkOrder(
            proposal_id="p2",
            session_id="sess-dry",
            approval_id="approval-2",
            approved_by="m",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="x",
            repo="yule-studio/naver-search-clone",
            dry_run=True,
            issue_auto_create_plan=self._sample_plan(),
        )
        self._enqueue(wo)
        outcome = self.worker.run_one()
        assert outcome is not None
        self.assertEqual(outcome.created_via, CREATED_VIA_DRY_RUN)
        self.assertIsNone(outcome.issue_number)
        anchor = self.sessions.sessions["sess-dry"].extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]
        self.assertEqual(anchor["created_via"], CREATED_VIA_DRY_RUN)
        self.assertIsNone(anchor["issue_number"])


# ---------------------------------------------------------------------------
# Existing anchor branch
# ---------------------------------------------------------------------------


class ExistingAnchorTests(_Fixture):
    def test_existing_issue_skips_writer(self) -> None:
        self.sessions.add("sess-reuse")
        wo = GitHubWorkOrder(
            proposal_id="p3",
            session_id="sess-reuse",
            approval_id="approval-3",
            approved_by="m",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="이미 있는 #42",
            repo="yule-studio/naver-search-clone",
            existing_issue_number=42,
        )
        self._enqueue(wo)
        outcome = self.worker.run_one()
        assert outcome is not None
        self.assertEqual(outcome.created_via, CREATED_VIA_EXISTING_ANCHOR)
        self.assertEqual(outcome.issue_number, 42)
        # writer 호출 안 됨
        self.assertEqual(self.writer.calls, [])
        # session stamp 확인
        anchor = self.sessions.sessions["sess-reuse"].extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]
        self.assertEqual(anchor["issue_number"], 42)
        self.assertEqual(anchor["created_via"], CREATED_VIA_EXISTING_ANCHOR)
        self.assertEqual(anchor["audit_reason"], "existing_issue_reused")


# ---------------------------------------------------------------------------
# Failure / skip branches
# ---------------------------------------------------------------------------


class FailureBranchTests(_Fixture):
    def test_missing_repo_fails_retryable(self) -> None:
        self.sessions.add("sess-no-repo")
        wo = GitHubWorkOrder(
            proposal_id="p4",
            session_id="sess-no-repo",
            approval_id="a4",
            approved_by="m",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="x",
            repo=None,
            issue_auto_create_plan=self._sample_plan(),
        )
        job = self._enqueue(wo)
        outcome = self.worker.run_one()
        assert outcome is not None
        self.assertEqual(outcome.skipped_reason, SKIPPED_NO_REPO)
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)
        self.assertEqual(refreshed.result.get("error"), SKIPPED_NO_REPO)
        # session 은 anchor 안 만듦
        self.assertNotIn(
            SESSION_EXTRA_GITHUB_ISSUE_KEY,
            self.sessions.sessions["sess-no-repo"].extra,
        )

    def test_missing_plan_and_no_existing_fails(self) -> None:
        self.sessions.add("sess-no-plan")
        wo = GitHubWorkOrder(
            proposal_id="p5",
            session_id="sess-no-plan",
            approval_id="a5",
            approved_by="m",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="x",
            repo="yule-studio/naver-search-clone",
            # 둘 다 비어있음
        )
        job = self._enqueue(wo)
        outcome = self.worker.run_one()
        assert outcome is not None
        self.assertEqual(outcome.skipped_reason, SKIPPED_MISSING_PLAN)
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)

    def test_missing_writer_fails(self) -> None:
        # writer_factory 가 None 을 반환 — production env 미준비 시뮬레이션
        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (None, "L2"),
            load_session_fn=lambda sid: self.sessions.load(sid),
            update_session_fn=lambda s, e: self.sessions.update(s, e),
        )
        self.sessions.add("sess-nw")
        wo = GitHubWorkOrder(
            proposal_id="p6",
            session_id="sess-nw",
            approval_id="a6",
            approved_by="m",
            approved_at="2026-05-15T10:00:00+00:00",
            request_summary="x",
            repo="yule-studio/naver-search-clone",
            issue_auto_create_plan=self._sample_plan(),
        )
        job = self._enqueue(wo)
        outcome = self.worker.run_one()
        assert outcome is not None
        self.assertEqual(outcome.skipped_reason, SKIPPED_NO_WRITER)
        refreshed = self.queue.get(job.job_id)
        self.assertEqual(refreshed.state, JobState.FAILED_RETRYABLE)
        self.assertEqual(refreshed.result.get("error"), SKIPPED_NO_WRITER)


# ---------------------------------------------------------------------------
# Queue drain semantics
# ---------------------------------------------------------------------------


class QueueDrainTests(_Fixture):
    def test_run_one_returns_none_when_queue_empty(self) -> None:
        outcome = self.worker.run_one()
        self.assertIsNone(outcome)


if __name__ == "__main__":
    unittest.main()
