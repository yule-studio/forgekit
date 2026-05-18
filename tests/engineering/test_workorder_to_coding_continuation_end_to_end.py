"""Work_order → coding_execute continuation 종단 회귀 (P0-S 마지막 단계).

contract — 한 줄로 묶인 시나리오:
  1. issue-less full-stack request
  2. approval card + 승인
  3. GitHubWorkOrderWorker drain → issue create + anchor stamp +
     continuation 자동 호출 → coding_job=ready 로 promote
  4. dispatcher (iter_ready_coding_jobs) 가 그 세션을 발견
  5. build_coding_execute_request 가 anchor 의 issue_number / repo 를
     CodingExecuteRequest 에 흘려보냄

추가 회귀:
  - existing_issue_number 만 있는 (template plan 없는) 케이스도 같은
    downstream path
  - 같은 work_order 가 두 번 drain 되면 두 번째는 coding_job=ready 가
    이미 있어 promote noop
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.job import STATUS_READY
from yule_orchestrator.agents.github_workos.audit import OUTCOME_OK
from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.job_queue.approval_worker import (
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.coding_execute_dispatcher import (
    build_coding_execute_request,
    iter_ready_coding_jobs,
)
from yule_orchestrator.agents.job_queue.github_work_order_executor import (
    GitHubWorkOrderWorker,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.job_queue.work_order_coding_continuation import (
    PROGRESS_CODING_DISPATCH_QUEUED,
    PROGRESS_CODING_JOB_READY,
    PROGRESS_ISSUE_CREATED,
    SESSION_EXTRA_CODING_JOB_KEY,
    SESSION_EXTRA_PROGRESS_KEY,
)
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    enqueue_github_work_approval,
    handle_github_work_approval_reply,
)


_FEATURE_TEMPLATE = (
    "---\n"
    'name: "[Feature] Issue Template"\n'
    'title: "[기능]"\n'
    'labels: "✨ Feature"\n'
    "---\n"
    "\n"
    "## 어떤 기능인가요?\n"
    "> 추가하려는 기능에 대해 간결하게 설명해주세요\n"
)


def _coding_proposal_payload(
    session_id: str = "sess-e2e",
    user_request: str = "Next.js + NestJS 회원가입 구현",
) -> Mapping[str, Any]:
    return {
        "session_id": session_id,
        "user_request": user_request,
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead", "qa-engineer"],
        "participant_roles": ["backend-engineer", "tech-lead", "qa-engineer"],
        "write_scope": ["src/api/auth", "web/src/app/login"],
        "forbidden_scope": ["secret", ".env"],
        "safety_rules": ["no force push"],
        "reason": "full-stack 회원가입 — backend-engineer 가 auth 책임",
        "approval_required": True,
        "metadata": {},
        "lifecycle_mode": "implementation",
    }


@dataclass
class _SessionStore:
    sessions: Dict[str, SimpleNamespace] = field(default_factory=dict)

    def make(self, session_id: str, *, request: str, with_proposal: bool = True) -> SimpleNamespace:
        extra = {
            "lifecycle_mode": "implementation",
            "active_research_roles": ["tech-lead", "backend-engineer"],
        }
        if with_proposal:
            extra["coding_proposal"] = _coding_proposal_payload(
                session_id=session_id, user_request=request
            )
        s = SimpleNamespace(session_id=session_id, extra=extra)
        self.sessions[session_id] = s
        return s

    def load(self, session_id: str):
        return self.sessions.get(session_id)

    def update(self, session, new_extra: Mapping[str, Any]):
        session.extra = dict(new_extra)
        self.sessions[session.session_id] = session
        return session


@dataclass
class _Call:
    title: str
    body: str
    labels: Tuple[str, ...]


class _StubIssueWriter:
    def __init__(self) -> None:
        self.calls: List[_Call] = []
        self._next = 77

    def create_issue(
        self,
        *,
        repo,
        title,
        body,
        labels=(),
        assignees=(),
        autonomy_level="L2",
        session_id=None,
        decision_id=None,
    ):
        self.calls.append(_Call(title=title, body=body, labels=tuple(labels)))
        n = self._next
        self._next += 1
        return SimpleNamespace(
            ok=True,
            outcome=OUTCOME_OK,
            succeeded=True,
            body={
                "number": n,
                "html_url": f"https://github.com/{repo}/issues/{n}",
                "url": f"https://api.github.com/repos/{repo}/issues/{n}",
            },
        )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ContinuationEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        self.posted: List[Tuple[ApprovalRequest, str]] = []

        async def _post(req, rendered):
            self.posted.append((req, rendered))
            return {"posted_message_id": 1}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post,
            channel_resolver=lambda: 99,
        )
        self.sessions = _SessionStore()
        self.writer = _StubIssueWriter()
        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            load_session_fn=lambda sid: self.sessions.load(sid),
            update_session_fn=lambda s, e: self.sessions.update(s, e),
        )

    def _drive_intake(
        self, *, session, request_text: str, existing_issue: Optional[int] = None
    ) -> ApprovalRequest:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        outcome = _run(
            enqueue_github_work_approval(
                session=session,
                request_text=request_text,
                approval_worker=self.approval_worker,
                repo="yule-studio/naver-search-clone",
                base_branch="main",
                repo_contract=rc,
                issue_template_loader=lambda p: _FEATURE_TEMPLATE,
                existing_issue_number=existing_issue,
            )
        )
        self.assertIsNotNone(outcome.proposal)
        return self.posted[-1][0]

    def test_issueless_request_ends_with_coding_job_ready(self) -> None:
        session = self.sessions.make(
            "sess-issueless",
            request="Next.js + NestJS 회원가입/검색 PR 올려",
        )
        approval_request = self._drive_intake(
            session=session,
            request_text="Next.js + NestJS 회원가입/검색 PR 올려",
        )

        # 승인 → work_order dispatch
        dispatched = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-e2e",
            approved_by="masterway",
            approved_at="2026-05-15T11:00:00+00:00",
            dry_run=False,
        )
        self.assertIsNone(dispatched.skipped_reason)

        # worker drain → issue create + continuation
        exec_outcome = self.worker.run_one()
        self.assertIsNotNone(exec_outcome)
        assert exec_outcome is not None
        self.assertEqual(exec_outcome.issue_number, 77)
        # writer 호출 1건
        self.assertEqual(len(self.writer.calls), 1)

        # session.extra 에 anchor + coding_job + progress markers 모두 있음
        s = self.sessions.sessions["sess-issueless"]
        self.assertIn(SESSION_EXTRA_GITHUB_ISSUE_KEY, s.extra)
        self.assertIn(SESSION_EXTRA_CODING_JOB_KEY, s.extra)
        coding_job = s.extra[SESSION_EXTRA_CODING_JOB_KEY]
        self.assertEqual(coding_job["status"], STATUS_READY)
        meta = coding_job["metadata"]
        self.assertEqual(meta["issue_number"], 77)
        self.assertEqual(meta["repo_full_name"], "yule-studio/naver-search-clone")
        self.assertEqual(meta["base_branch"], "main")
        self.assertEqual(meta["approval_id"], "approval-e2e")
        self.assertEqual(meta["approved_by"], "masterway")
        # progress markers — P0-Y: ready 단계는 coding_job_ready 만 stamp.
        # 실제 queue row 가 만들어진 시점은 dispatcher (다음 producer tick)
        # 에서 coding_dispatch_queued 가 stamp 된다.
        progress = s.extra[SESSION_EXTRA_PROGRESS_KEY]
        self.assertIn(PROGRESS_ISSUE_CREATED, progress)
        self.assertIn(PROGRESS_CODING_JOB_READY, progress)
        self.assertNotIn(PROGRESS_CODING_DISPATCH_QUEUED, progress)
        # work_order queue row 의 result 에 continuation 흔적
        row = exec_outcome.job
        assert row is not None
        self.assertTrue(row.result.get("coding_dispatch_queued"))
        self.assertIsNone(row.result.get("coding_dispatch_noop_reason"))

    def test_dispatcher_picks_up_promoted_session(self) -> None:
        """ready 로 promote 된 세션이 iter_ready_coding_jobs 의 pick 대상."""

        session = self.sessions.make(
            "sess-pick", request="full-stack 구현 PR 올려"
        )
        approval_request = self._drive_intake(
            session=session, request_text="full-stack 구현 PR 올려"
        )
        handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-pick",
            approved_by="m",
            approved_at="2026-05-15T11:00:00+00:00",
            dry_run=False,
        )
        self.worker.run_one()

        # iter_ready_coding_jobs 가 그 세션을 yield
        ready_jobs = list(
            iter_ready_coding_jobs(
                session_loader=lambda: list(self.sessions.sessions.values())
            )
        )
        self.assertEqual(len(ready_jobs), 1)
        ready = ready_jobs[0]
        self.assertEqual(ready.session_id, "sess-pick")
        self.assertEqual(ready.executor_role(), "backend-engineer")

        # build_coding_execute_request 가 anchor 의 repo/issue_number 를
        # CodingExecuteRequest 에 흘려보냄
        request = build_coding_execute_request(ready, env={})
        self.assertEqual(request.issue_number, 77)
        self.assertEqual(
            request.repo_full_name, "yule-studio/naver-search-clone"
        )
        self.assertEqual(request.base_branch, "main")
        self.assertEqual(request.executor_role, "backend-engineer")
        self.assertFalse(request.dry_run)

    def test_existing_issue_continuation_uses_same_path(self) -> None:
        session = self.sessions.make(
            "sess-existing", request="issue #42 작업 PR 올려"
        )
        approval_request = self._drive_intake(
            session=session,
            request_text="issue #42 작업 PR 올려",
            existing_issue=42,
        )
        handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-existing",
            approved_by="m",
            approved_at="2026-05-15T11:00:00+00:00",
            dry_run=False,
        )
        exec_outcome = self.worker.run_one()
        assert exec_outcome is not None
        # writer 호출 없음 (existing anchor)
        self.assertEqual(self.writer.calls, [])
        self.assertEqual(exec_outcome.issue_number, 42)
        # 하지만 coding_job=ready 는 똑같이 stamp
        s = self.sessions.sessions["sess-existing"]
        coding_job = s.extra[SESSION_EXTRA_CODING_JOB_KEY]
        self.assertEqual(coding_job["status"], STATUS_READY)
        self.assertEqual(coding_job["metadata"]["issue_number"], 42)
        progress = s.extra[SESSION_EXTRA_PROGRESS_KEY]
        self.assertIn(PROGRESS_CODING_JOB_READY, progress)
        self.assertNotIn(PROGRESS_CODING_DISPATCH_QUEUED, progress)

    def test_same_work_order_drained_twice_does_not_repromote(self) -> None:
        """idempotency: 같은 work_order job 이 두 번 drain (e.g., worker
        restart) 되어도 coding_job 이 중복 promote 되지 않는다.

        본 테스트는 queue 의 unique work_order job 자체는 한 번만 SAVED
        되지만, anchor 가 있는 session.extra 에 promote 가 한 번 더 시도
        되어도 continuation 이 already_ready_same_anchor 로 noop 반환하는
        것을 핀.
        """

        from yule_orchestrator.agents.job_queue.work_order_coding_continuation import (
            CONTINUATION_NOOP_ALREADY_READY,
            promote_session_to_coding_ready,
        )

        session = self.sessions.make(
            "sess-idem", request="PR 올려"
        )
        approval_request = self._drive_intake(
            session=session, request_text="PR 올려"
        )
        handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-idem",
            approved_by="m",
            approved_at="2026-05-15T11:00:00+00:00",
            dry_run=False,
        )
        self.worker.run_one()

        # 두 번째 promote 시도 — anchor 와 session.extra 가 이미 ready
        s = self.sessions.sessions["sess-idem"]
        anchor = s.extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]
        outcome = promote_session_to_coding_ready(
            session_extra=s.extra,
            anchor=anchor,
            repo="yule-studio/naver-search-clone",
            base_branch="main",
            dry_run=False,
            approval_id="approval-idem",
            approved_by="m",
            approved_at="2026-05-15T11:00:00+00:00",
        )
        self.assertFalse(outcome.promoted)
        self.assertEqual(outcome.noop_reason, CONTINUATION_NOOP_ALREADY_READY)


if __name__ == "__main__":
    unittest.main()
