"""Adapter 의 issue-less bootstrap wiring 회귀 — P0-S 종단.

contract:
  - repo_contract + issue_template_loader 가 주어지면 proposal 의
    issue_auto_create_plan 이 채워진다.
  - session.extra 에 이미 anchor 가 있으면 plan 은 None, existing_issue
    번호는 anchor 에서 가져온다 (중복 생성 금지).
  - 명시적 existing_issue_number 가 더 우선한다.
  - 카드 본문 extras 에 `issue_auto_create_audit_reason` 이 남는다.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.job_queue.approval_worker import (
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    build_github_work_order_proposal,
    enqueue_github_work_approval,
)


_FEATURE_TEMPLATE = (
    "---\n"
    'name: "[Feature] Issue Template"\n'
    'title: "[Feature]"\n'
    'labels: "✨ Feature, 📃 Docs"\n'
    "---\n"
    "\n"
    "## 어떤 기능인가요?\n"
    "> 추가하려는 기능에 대해 간결하게 설명해주세요\n"
)


def _session(*, session_id="sess-x", extra: Optional[dict] = None):
    base = {
        "lifecycle_mode": "implementation",
        "active_research_roles": ["tech-lead", "backend-engineer"],
    }
    if extra:
        base.update(extra)
    return SimpleNamespace(session_id=session_id, extra=base)


class BuildProposalIssuePlanTests(unittest.TestCase):
    def test_plan_stamped_when_no_existing_issue(self) -> None:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text="Next.js + NestJS 회원가입 PR 올려줘",
            repo="yule-studio/naver-search-clone",
            repo_contract=rc,
            issue_template_loader=lambda p: _FEATURE_TEMPLATE
            if p.endswith("feature.md")
            else None,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertIsNotNone(proposal.issue_auto_create_plan)
        assert proposal.issue_auto_create_plan is not None
        plan = proposal.issue_auto_create_plan
        self.assertEqual(plan["audit_reason"], "template_used")
        self.assertTrue(plan["title"].startswith("[Feature]"))
        self.assertIn("✨ Feature", plan["labels"])
        # extras 에 audit_reason 흔적
        self.assertEqual(
            proposal.extra.get("issue_auto_create_audit_reason"),
            "template_used",
        )

    def test_explicit_existing_issue_short_circuits(self) -> None:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text="issue #99 작업 진행 PR 올려",
            repo="yule-studio/naver-search-clone",
            repo_contract=rc,
            issue_template_loader=lambda p: _FEATURE_TEMPLATE,
            existing_issue_number=99,
        )
        assert proposal is not None
        self.assertEqual(proposal.existing_issue_number, 99)
        self.assertIsNone(proposal.issue_auto_create_plan)

    def test_session_anchor_is_reused_when_no_explicit(self) -> None:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        session = _session(
            extra={"github_work_order_issue": {"issue_number": 42}}
        )
        proposal = build_github_work_order_proposal(
            session=session,
            request_text="기존 작업 이어서 PR 올려",
            repo="yule-studio/naver-search-clone",
            repo_contract=rc,
            issue_template_loader=lambda p: _FEATURE_TEMPLATE,
        )
        assert proposal is not None
        self.assertEqual(proposal.existing_issue_number, 42)
        self.assertIsNone(proposal.issue_auto_create_plan)

    def test_no_repo_contract_falls_back_to_minimal_plan(self) -> None:
        # P0-U live smoke fix — repo_contract 미주입이라도 *repo 만* 주어지면
        # minimal RepoContract 를 즉석에서 구성해 default fallback plan 을
        # 생성한다. 이전엔 plan 이 None 으로 남아 executor 가
        # `github_work_order_missing_plan_or_issue` 로 떨어졌다.
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text="버그 고쳐서 PR 올려",
            repo="yule-studio/naver-search-clone",
        )
        assert proposal is not None
        self.assertIsNotNone(proposal.issue_auto_create_plan)
        self.assertIsNone(proposal.existing_issue_number)
        # caller 가 본격 repo_contract 없이 주입했다는 marker 가 extras 에 stamp
        self.assertEqual(
            (proposal.extra or {}).get("issue_auto_create_contract_source"),
            "minimal_repo_string",
        )

    def test_no_repo_and_no_repo_contract_keeps_plan_none(self) -> None:
        # repo / repo_contract 둘 다 없으면 plan 은 여전히 None — 이 경우는
        # caller 가 repo 정보 자체를 안 가지고 있는 상황이라 fallback 의미
        # 없음.
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text="버그 고쳐서 PR 올려",
            repo=None,
        )
        assert proposal is not None
        self.assertIsNone(proposal.issue_auto_create_plan)
        self.assertIsNone(proposal.existing_issue_number)

    def test_ambiguous_template_marks_extra_flag(self) -> None:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(
                ".github/ISSUE_TEMPLATE/feature.md",
                ".github/ISSUE_TEMPLATE/bug.md",
            ),
        )
        # 두 template 모두 매칭 0 점수 — LOW confidence + needs_operator_decision
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text="zzz yyy xxx PR 올려",  # 매칭 키워드 없음
            repo="yule-studio/naver-search-clone",
            repo_contract=rc,
            issue_template_loader=lambda p: _FEATURE_TEMPLATE,
        )
        assert proposal is not None
        # plan 은 생성 (LOW confidence 라도 filled), needs_decision 플래그가 extras 에 남음
        self.assertIsNotNone(proposal.issue_auto_create_plan)
        self.assertTrue(
            proposal.extra.get("issue_auto_create_needs_decision") is True
        )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class EnqueueApprovalIssuePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=self.db)
        self.heartbeats = HeartbeatStore(db_path=self.db)
        self.posted: list = []

        async def _post_fn(req, rendered):
            self.posted.append((req, rendered))
            return {"posted_message_id": 1234}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: 99,
        )

    def test_approval_card_carries_plan_in_extra(self) -> None:
        rc = RepoContract(
            owner="yule-studio",
            repo="naver-search-clone",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        outcome = _run(
            enqueue_github_work_approval(
                session=_session(),
                request_text="회원가입 구현 PR 올려",
                approval_worker=self.approval_worker,
                repo="yule-studio/naver-search-clone",
                repo_contract=rc,
                issue_template_loader=lambda p: _FEATURE_TEMPLATE,
            )
        )
        self.assertIsNotNone(outcome.proposal)
        assert outcome.proposal is not None
        # 카드 1 건이 게시됐고, payload extras 에 proposal 사본 + plan 포함
        self.assertEqual(len(self.posted), 1)
        request, _rendered = self.posted[0]
        proposal_payload = request.extra.get("github_work_order_proposal") or {}
        plan_payload = proposal_payload.get("issue_auto_create_plan")
        self.assertIsNotNone(plan_payload)
        assert plan_payload is not None
        self.assertEqual(plan_payload["audit_reason"], "template_used")
        self.assertIn("✨ Feature", plan_payload["labels"])


if __name__ == "__main__":
    unittest.main()
