"""Issue-less bootstrap end-to-end — P0-S 회사형 runtime 종단 회귀.

다음 시나리오를 한 줄로 묶어 검증한다:

1. operator 가 Discord 에 issue 가 없는 full-stack coding request 를 던짐.
2. RepoContract 가 ISSUE_TEMPLATE 발견.
3. adapter 가 issue_auto_create_plan 을 stamp 한 proposal 빌드.
4. approval 카드가 `#승인-대기` 로 게시.
5. operator 가 approve → handle_github_work_approval_reply 가 work_order
   dispatch.
6. GitHubWorkOrderWorker 가 work_order job 을 drain → writer.create_issue
   호출 → 결과 issue number/url 을 session.extra 에 stamp.
7. 같은 session 으로 다시 build_github_work_order_proposal 을 호출하면
   anchor 가 재사용돼 중복 issue 가 생성되지 않음 (existing anchor 전환).

이 테스트가 통과하면 "issue 없는 repo 에 업무 요청만 들어와도 agent 가
스스로 issue 를 만들고 그것을 anchor 로 끝까지 이어가는" 종단의 핵심
경로가 닫혀 있다.
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

from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.github_workos.audit import OUTCOME_OK
from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.github_work_order import (
    APPROVAL_KIND_GITHUB_WORK_ORDER,
    GitHubWorkOrderProposal,
)
from yule_orchestrator.agents.job_queue.github_work_order_executor import (
    CREATED_VIA_AUTO_CREATE,
    CREATED_VIA_EXISTING_ANCHOR,
    GitHubWorkOrderWorker,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    build_github_work_order_proposal,
    enqueue_github_work_approval,
    handle_github_work_approval_reply,
)


_FEATURE_TEMPLATE = (
    "---\n"
    'name: "[Feature] Issue Template"\n'
    'title: "[기능]"\n'
    'labels: "✨ Feature, 📃 Docs"\n'
    "---\n"
    "\n"
    "## 어떤 기능인가요?\n"
    "> 추가하려는 기능에 대해 간결하게 설명해주세요\n"
)


@dataclass
class _SessionStore:
    sessions: Dict[str, Any] = field(default_factory=dict)

    def make(self, session_id: str) -> SimpleNamespace:
        session = SimpleNamespace(
            session_id=session_id,
            extra={
                "lifecycle_mode": "implementation",
                "active_research_roles": ["tech-lead", "backend-engineer"],
            },
        )
        self.sessions[session_id] = session
        return session

    def load(self, session_id: str):
        return self.sessions.get(session_id)

    def update(self, session, new_extra: Mapping[str, Any]):
        session.extra = dict(new_extra)
        self.sessions[session.session_id] = session
        return session


@dataclass
class _WriterCall:
    title: str
    body: str
    labels: Tuple[str, ...]
    assignees: Tuple[str, ...]


class _StubIssueWriter:
    """Minimal writer Protocol — captures create_issue calls."""

    def __init__(self) -> None:
        self.calls: List[_WriterCall] = []
        self._next_number = 77

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
            _WriterCall(
                title=title,
                body=body,
                labels=tuple(labels),
                assignees=tuple(assignees),
            )
        )
        number = self._next_number
        self._next_number += 1
        result = SimpleNamespace(
            ok=True,
            outcome=OUTCOME_OK,
            succeeded=True,
            body={
                "number": number,
                "html_url": f"https://github.com/{repo}/issues/{number}",
                "url": f"https://api.github.com/repos/{repo}/issues/{number}",
            },
        )
        return result


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class IssuelessBootstrapEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db_path)
        self.heartbeats = HeartbeatStore(db_path=db_path)
        self.posted: List[Tuple[ApprovalRequest, str]] = []

        async def _post(req, rendered):
            self.posted.append((req, rendered))
            return {"posted_message_id": 9999}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post,
            channel_resolver=lambda: 4242,
        )
        self.sessions = _SessionStore()
        self.writer = _StubIssueWriter()
        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            load_session_fn=lambda sid: self.sessions.load(sid),
            update_session_fn=lambda s, e: self.sessions.update(s, e),
        )

    def test_issueless_request_flows_to_real_issue_anchor(self) -> None:
        session = self.sessions.make("sess-end-to-end")
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )

        # 1. approval card 게시 + proposal 에 plan stamp
        outcome = _run(
            enqueue_github_work_approval(
                session=session,
                request_text=(
                    "Next.js + NestJS + PostgreSQL 회원가입/로그인/검색 PR 올려"
                ),
                approval_worker=self.approval_worker,
                repo="yule-studio/naver-search-clone",
                source_message_id=12345,
                repo_contract=rc,
                issue_template_loader=lambda p: _FEATURE_TEMPLATE,
                requested_by="masterway",
            )
        )
        self.assertIsNotNone(outcome.proposal)
        self.assertEqual(len(self.posted), 1)
        approval_request, _rendered = self.posted[0]
        proposal_payload = approval_request.extra.get(
            "github_work_order_proposal"
        )
        self.assertIsInstance(proposal_payload, Mapping)
        plan = proposal_payload.get("issue_auto_create_plan")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan["title"].startswith("[기능]"))

        # 2. operator 승인 → work order dispatch
        dispatch_outcome = handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-e2e",
            approved_by="masterway",
            approved_at="2026-05-15T11:00:00+00:00",
            dry_run=False,
        )
        self.assertIsNone(dispatch_outcome.skipped_reason)
        self.assertIsNotNone(dispatch_outcome.dispatched_job_id)

        # 3. worker drain → 실제 issue 생성
        exec_outcome = self.worker.run_one()
        self.assertIsNotNone(exec_outcome)
        assert exec_outcome is not None
        self.assertEqual(exec_outcome.created_via, CREATED_VIA_AUTO_CREATE)
        self.assertEqual(exec_outcome.issue_number, 77)
        self.assertTrue(
            exec_outcome.issue_url
            and "yule-studio/naver-search-clone/issues/77" in exec_outcome.issue_url
        )

        # 4. session.extra 에 anchor stamp 됐는지
        anchor = session.extra.get(SESSION_EXTRA_GITHUB_ISSUE_KEY)
        self.assertIsInstance(anchor, dict)
        assert isinstance(anchor, dict)
        self.assertEqual(anchor["issue_number"], 77)
        self.assertEqual(anchor["created_via"], CREATED_VIA_AUTO_CREATE)
        self.assertEqual(anchor["approval_id"], "approval-e2e")

        # 5. 같은 session 으로 다시 proposal 만들면 anchor 재사용
        again = build_github_work_order_proposal(
            session=session,
            request_text="추가 PR 올려줘 — 검색 결과 페이지 보강 구현",
            repo="yule-studio/naver-search-clone",
            repo_contract=rc,
            issue_template_loader=lambda p: _FEATURE_TEMPLATE,
        )
        self.assertIsNotNone(again)
        assert again is not None
        self.assertEqual(again.existing_issue_number, 77)
        self.assertIsNone(again.issue_auto_create_plan)

    def test_existing_issue_number_in_request_skips_plan_creation(self) -> None:
        """operator 가 명시한 issue 번호가 있으면 plan 빌드 자체를 건너뜀."""

        session = self.sessions.make("sess-explicit")
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        outcome = _run(
            enqueue_github_work_approval(
                session=session,
                request_text="이슈 #15 작업 PR 올려",
                approval_worker=self.approval_worker,
                repo="yule-studio/naver-search-clone",
                repo_contract=rc,
                issue_template_loader=lambda p: _FEATURE_TEMPLATE,
                existing_issue_number=15,
            )
        )
        self.assertIsNotNone(outcome.proposal)
        assert outcome.proposal is not None
        self.assertEqual(outcome.proposal.existing_issue_number, 15)
        self.assertIsNone(outcome.proposal.issue_auto_create_plan)
        approval_request, _ = self.posted[-1]
        # approval → dispatch → worker
        handle_github_work_approval_reply(
            queue=self.queue,
            approval_request=approval_request,
            approval_id="approval-explicit",
            approved_by="masterway",
            approved_at="2026-05-15T12:00:00+00:00",
            dry_run=False,
        )
        exec_outcome = self.worker.run_one()
        self.assertIsNotNone(exec_outcome)
        assert exec_outcome is not None
        # writer 호출 안 됨
        self.assertEqual(self.writer.calls, [])
        self.assertEqual(exec_outcome.created_via, CREATED_VIA_EXISTING_ANCHOR)
        self.assertEqual(exec_outcome.issue_number, 15)
        anchor = session.extra.get(SESSION_EXTRA_GITHUB_ISSUE_KEY)
        assert isinstance(anchor, dict)
        self.assertEqual(anchor["issue_number"], 15)
        self.assertEqual(anchor["created_via"], CREATED_VIA_EXISTING_ANCHOR)


if __name__ == "__main__":
    unittest.main()
