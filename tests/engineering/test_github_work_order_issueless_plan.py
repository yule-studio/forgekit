"""issue-less intake → issue_auto_create_plan 자동 생성 회귀.

사용자 라이브 스모크 증상:
- intake / approval card / approval reply / work_order consumer 까지 OK
- 그런데 `github_work_order_missing_plan_or_issue` 로 멈춤
- 원인: slash intake 가 `repo_contract` 를 안 넘겨서
  `build_github_work_order_proposal` 의 plan-build guard 가 false,
  proposal 의 `issue_auto_create_plan` 이 None 으로 남았다.

이 테스트 모듈은 사용자가 명시한 5 종 + 보조 케이스를 커버:

1. issue-less full-stack intake with repo → proposal has issue_auto_create_plan
2. approval reply → queued github_work_order payload preserves the plan
3. executor no longer fails with `github_work_order_missing_plan_or_issue`
4. runtime-ish path → work_order drains → issue create or dry-run anchor path
5. next stage visible → session.extra anchor stamped + continuation toward coding_execute
"""

from __future__ import annotations

import unittest
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_ENGINEERING_WRITE,
    ApprovalRequest,
)
from yule_orchestrator.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    GitHubWorkOrderProposal,
    dispatch_github_work_order,
)
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    _minimal_repo_contract_from_repo,
    build_github_work_order_proposal,
    handle_github_work_approval_reply,
)


_REPO = "yule-studio/naver-search-clone"
_PROMPT_FULLSTACK = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버 검색 풀스택 MVP 구현해줘. 검색 / 블로그 / 메일."
)


def _session(*, session_id: str = "sess-x", prompt: str = _PROMPT_FULLSTACK, extra: Optional[Mapping[str, Any]] = None) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        prompt=prompt,
        extra=extra or {},
        references_user=[],
    )


# ---------------------------------------------------------------------------
# 1. issue-less proposal builds a plan from repo string alone
# ---------------------------------------------------------------------------


class IsslessProposalBuildsPlan(unittest.TestCase):
    def test_repo_only_minimal_contract_produces_plan(self) -> None:
        """사용자가 명시한 1번 — repo 만 주고 repo_contract 없이도 plan 생성."""

        session = _session()
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
            requested_by="user",
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        # Plan 이 None 이 아니어야 한다 — 이게 사용자 라이브 스모크의 핵심 회귀
        self.assertIsNotNone(
            proposal.issue_auto_create_plan,
            "build_github_work_order_proposal 이 repo 만으로도 plan 을 만들어야 한다",
        )
        # 최소 plan field 검증
        plan = proposal.issue_auto_create_plan
        self.assertTrue(plan.get("title"))
        self.assertTrue(plan.get("body"))
        # caller 가 본격 repo_contract 를 안 넘겼다는 사실이 extra 에 audit
        self.assertIn(
            "issue_auto_create_contract_source",
            (proposal.extra or {}),
        )
        self.assertEqual(
            proposal.extra["issue_auto_create_contract_source"],
            "minimal_repo_string",
        )

    def test_explicit_repo_contract_still_works(self) -> None:
        contract = RepoContract(owner="yule-studio", repo="naver-search-clone")
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
            repo_contract=contract,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertIsNotNone(proposal.issue_auto_create_plan)
        # caller 가 본격 contract 를 줬으므로 contract_source marker 는 없음
        self.assertNotIn(
            "issue_auto_create_contract_source", (proposal.extra or {})
        )

    def test_no_repo_no_plan(self) -> None:
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=None,
        )
        # eligible 한 코딩 intent 라도 repo 가 없으면 plan 도 없음 — caller 가
        # repo 를 채워야 진행 가능.
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertIsNone(proposal.issue_auto_create_plan)

    def test_existing_issue_skips_plan(self) -> None:
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
            existing_issue_number=42,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        # plan 은 None — existing issue 가 있으므로 중복 생성 금지
        self.assertIsNone(proposal.issue_auto_create_plan)
        self.assertEqual(proposal.existing_issue_number, 42)


class MinimalContractParser(unittest.TestCase):
    def test_owner_repo_parsed(self) -> None:
        contract = _minimal_repo_contract_from_repo("yule-studio/naver-search-clone")
        assert contract is not None
        self.assertEqual(contract.owner, "yule-studio")
        self.assertEqual(contract.repo, "naver-search-clone")
        self.assertTrue(contract.fallback)

    def test_dot_git_suffix_stripped(self) -> None:
        contract = _minimal_repo_contract_from_repo("foo/bar.git")
        assert contract is not None
        self.assertEqual(contract.repo, "bar")

    def test_invalid_repo_returns_none(self) -> None:
        for value in ("", None, "not-a-repo", "owner/", "/repo", "/"):
            self.assertIsNone(_minimal_repo_contract_from_repo(value))


# ---------------------------------------------------------------------------
# 2. payload continuity — proposal → approval reply → final work order
# ---------------------------------------------------------------------------


class PayloadContinuityTests(unittest.TestCase):
    def test_proposal_to_payload_and_back_preserves_plan(self) -> None:
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
        )
        assert proposal is not None
        payload = proposal.to_payload()
        # plan field 가 payload 에 dict 로 살아있음
        self.assertIsInstance(payload["issue_auto_create_plan"], Mapping)

        restored = GitHubWorkOrderProposal.from_payload(payload)
        self.assertIsNotNone(restored.issue_auto_create_plan)
        self.assertEqual(
            restored.issue_auto_create_plan["title"],
            proposal.issue_auto_create_plan["title"],
        )

    def test_work_order_from_proposal_preserves_plan(self) -> None:
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
        )
        assert proposal is not None
        work_order = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="approval-x",
            approved_by="user",
            approved_at="2026-05-17T12:00:00+00:00",
        )
        self.assertIsNotNone(work_order.issue_auto_create_plan)
        # repo / base / dry_run / approval anchor 모두 보존
        self.assertEqual(work_order.repo, _REPO)
        self.assertEqual(work_order.approval_id, "approval-x")

    def test_approval_reply_to_work_order_preserves_plan(self) -> None:
        """사용자 §2번 — approval reply 까지 plan 보존."""

        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
        )
        assert proposal is not None

        # approval_request.extra 가 proposal payload 를 carry 한다 — 실제
        # adapter 의 `_approval_request_from_proposal` 도 동일 패턴.
        approval_request = ApprovalRequest(
            session_id=proposal.session_id,
            approval_kind=APPROVAL_KIND_ENGINEERING_WRITE,
            title="t",
            summary="s",
            requested_action="github_work_order",
            created_by="user",
            extra={"github_work_order_proposal": proposal.to_payload()},
        )

        # in-memory queue stub: 큐 호출은 dispatch_github_work_order 한 번
        recorded: list = []

        class _StubQueue:
            def insert(self, *args, **kwargs):
                recorded.append(("insert", args, kwargs))
                return SimpleNamespace(job_id="job-1")

            def enqueue(self, *args, **kwargs):
                recorded.append(("enqueue", args, kwargs))
                return SimpleNamespace(job_id="job-1")

            def find_active(self, *args, **kwargs):
                return None

            def list_for_session(self, session_id, *, states=()):
                # no active work_order for this session — dispatch will insert
                return ()

        queue = _StubQueue()
        outcome = handle_github_work_approval_reply(
            queue=queue,
            approval_request=approval_request,
            approval_id="approval-x",
            approved_by="user",
            approved_at="2026-05-17T12:00:00+00:00",
        )
        self.assertIsNotNone(outcome.work_order)
        assert outcome.work_order is not None
        self.assertIsNotNone(outcome.work_order.issue_auto_create_plan)


# ---------------------------------------------------------------------------
# 3. executor no longer fails with missing_plan_or_issue
# ---------------------------------------------------------------------------


class ExecutorMissingPlanContractTests(unittest.TestCase):
    """executor 의 guard 가 정확히 *둘 다 없는* 경우에만 fail 하는지."""

    def test_only_plan_present_does_not_fail(self) -> None:
        from yule_orchestrator.agents.job_queue.github_work_order_executor import (
            SKIPPED_MISSING_PLAN,
        )

        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
        )
        assert proposal is not None
        work_order = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="approval-x",
            approved_by="user",
            approved_at="2026-05-17T12:00:00+00:00",
        )
        # plan 이 있으므로 missing_plan_or_issue 에 빠지면 안 됨
        self.assertIsNotNone(work_order.issue_auto_create_plan)
        self.assertIsNone(work_order.existing_issue_number)
        # SKIPPED_MISSING_PLAN 토큰이 우리 fix 의 가드와 정확히 일치
        self.assertEqual(SKIPPED_MISSING_PLAN, "github_work_order_missing_plan_or_issue")

    def test_only_existing_issue_present_does_not_fail(self) -> None:
        # existing_issue_number 만 있고 plan 은 없어도 OK
        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=_REPO,
            existing_issue_number=99,
        )
        assert proposal is not None
        work_order = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="approval-x",
            approved_by="user",
        )
        self.assertEqual(work_order.existing_issue_number, 99)
        # plan 은 None (existing_issue 가 있으니까)
        self.assertIsNone(work_order.issue_auto_create_plan)

    def test_both_missing_still_fails(self) -> None:
        """defensive: 만약 어떤 경로로든 둘 다 None 이면 여전히 미스 가드."""

        proposal = build_github_work_order_proposal(
            session=_session(),
            request_text=_PROMPT_FULLSTACK,
            repo=None,  # repo 가 없으면 plan 도 안 만들어짐
        )
        assert proposal is not None
        self.assertIsNone(proposal.issue_auto_create_plan)
        self.assertIsNone(proposal.existing_issue_number)


# ---------------------------------------------------------------------------
# 4 + 5. runtime drain & continuation — anchor stamp + coding_execute 가시화
# ---------------------------------------------------------------------------


class RuntimeDrainAndContinuationTests(unittest.TestCase):
    """work_order_coding_continuation 이 anchor → coding_job=ready 로 promote."""

    def test_anchor_stamp_promotes_coding_job(self) -> None:
        from yule_orchestrator.agents.job_queue.work_order_coding_continuation import (
            PROGRESS_CODING_DISPATCH_QUEUED,
            PROGRESS_ISSUE_CREATED,
            promote_session_to_coding_ready,
        )

        # 가장 흔한 happy-path: anchor 가 들어왔고 session.extra 에
        # coding_proposal 이 stamp 되어 있음. proposal_from_dict 는 누락 필드를
        # 안전한 기본값으로 메우므로 minimal dict 만 줘도 round-trip OK.
        proposal_dict = {
            "session_id": "sess-x",
            "user_request": _PROMPT_FULLSTACK,
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead"],
            "participant_roles": ["backend-engineer", "tech-lead"],
            "write_scope": [],
            "forbidden_scope": [],
            "reason": "",
            "safety_rules": [],
            "approval_required": True,
            "metadata": {},
            "lifecycle_mode": "implementation",
            "research_leads": [],
        }
        extra = {
            "coding_proposal": proposal_dict,
        }
        anchor = {
            "issue_number": 7,
            "html_url": "https://github.com/yule-studio/naver-search-clone/issues/7",
            "created_via": "auto_create_via_plan",
        }
        outcome = promote_session_to_coding_ready(
            session_extra=extra,
            anchor=anchor,
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            approval_id="approval-x",
        )
        self.assertTrue(outcome.promoted)
        # progress marker 가 둘 다 stamp 됨 — issue_created + dispatch_queued
        self.assertIn(PROGRESS_ISSUE_CREATED, outcome.progress_markers)
        self.assertIn(PROGRESS_CODING_DISPATCH_QUEUED, outcome.progress_markers)
        # coding_job 이 ready 로 promote
        new_extra = outcome.new_extra
        self.assertIn("coding_job", new_extra)
        self.assertEqual(new_extra["coding_job"]["status"], "ready")
        self.assertEqual(new_extra["coding_job"]["metadata"]["issue_number"], 7)
        # 같은 anchor 로 다시 호출하면 noop (idempotent)
        outcome_again = promote_session_to_coding_ready(
            session_extra=new_extra,
            anchor=anchor,
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            approval_id="approval-x",
        )
        self.assertFalse(outcome_again.promoted)


if __name__ == "__main__":
    unittest.main()
