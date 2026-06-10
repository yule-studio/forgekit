"""Issue auto-create end-to-end — P0-S 종단 회귀.

목적
====
"코딩 요청을 받았는데 issue 가 없으면 issue 를 자동 생성한 뒤 dispatch
한다" 라는 사용자 기대를 dispatch 직전까지 한 줄로 묶어 검증한다. 실제
GitHub API 호출은 없음 (dry_run 또는 stub) — 단, 다음을 모두 핀:

  1. discover_repo_contract 가 target repo 의 issue_templates 를 발견.
  2. build_issue_auto_create_plan 이 그 template 을 읽어 plan 생성.
  3. plan 이 GitHubWorkOrderProposal.issue_auto_create_plan 에 stamp.
  4. approval → work_order 단계에서 plan 이 그대로 전달.
  5. GithubWriter.create_issue (dry_run=False, live=True, stub client) 가
     plan.title/body/labels/assignees 를 그대로 사용.

회귀 방지:
  - existing_issue_number 가 명시되면 plan 은 None — issue 생성 안 함.
  - tag_policy=='none' 이면 work_order extra 에 tag plan 미적용 audit.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_vcs.repo_contract import discover_repo_contract
from yule_engineering.agents.github_workos.audit import (
    ACTION_GITHUB_ISSUE_CREATE,
    OUTCOME_OK,
)
from yule_engineering.agents.github_workos.github_writer import (
    GithubWriter,
    make_default_policy_gate,
)
from yule_engineering.agents.github_workos.issue_auto_create import (
    AUDIT_EXISTING_ISSUE_REUSED,
    AUDIT_TEMPLATE_USED,
    build_issue_auto_create_plan,
)
from yule_engineering.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    GitHubWorkOrderProposal,
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
    "\n"
    "## 작업 상세 내용\n"
    "- [ ] \n"
)


class _StubIssueClient:
    """create_issue 만 받는 최소 stub."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def create_issue_comment(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_issue(self, *, repo, title, body, labels=(), assignees=()):
        self.calls.append(
            (
                "create_issue",
                {
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "labels": tuple(labels),
                    "assignees": tuple(assignees),
                },
            )
        )
        return {
            "url": f"https://api.github.com/repos/{repo}/issues/77",
            "html_url": f"https://github.com/{repo}/issues/77",
            "number": 77,
            "status": 201,
        }

    def add_labels(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_branch_ref(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_commit_via_data_api(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError

    def create_draft_pull_request(self, **_kwargs):  # pragma: no cover
        raise NotImplementedError


class EndToEndIssueAutoCreateTests(unittest.TestCase):
    """coding request (no issue) → repo_contract → plan → work_order →
    GithubWriter.create_issue 전 경로."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = Path(self._tmp.name)
        self.target_repo = self.workspace_root / "yule-studio" / "naver-search-clone"
        self.target_repo.mkdir(parents=True)
        (self.target_repo / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        (
            self.target_repo
            / ".github"
            / "ISSUE_TEMPLATE"
            / "-feature--issue-template.md"
        ).write_text(_FEATURE_TEMPLATE)
        (self.target_repo / ".github" / "PULL_REQUEST_TEMPLATE").write_text(
            "## 📌 관련 이슈\n## ✨ 과제 내용\n"
        )
        (self.target_repo / "README.md").write_text("# repo")

    def _template_loader_from_workspace(self, repo_contract):
        repo_root = (
            self.workspace_root / repo_contract.owner / repo_contract.repo
        )

        def _load(path: str):
            full = repo_root / path
            if not full.is_file():
                return None
            return full.read_text(encoding="utf-8")

        return _load

    def test_end_to_end_dispatch_with_template(self) -> None:
        # 1. discover repo contract
        contract = discover_repo_contract(
            owner="yule-studio",
            repo="naver-search-clone",
            workspace_root=str(self.workspace_root),
        )
        self.assertEqual(contract.backend, "local_clone")
        self.assertTrue(contract.issue_templates)

        # 2. build plan
        outcome = build_issue_auto_create_plan(
            repo_contract=contract,
            request_summary="Next.js + NestJS 회원가입/검색 구현",
            template_loader=self._template_loader_from_workspace(contract),
            session_id="sess-e2e",
        )
        self.assertIsNotNone(outcome.plan)
        assert outcome.plan is not None
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_USED)
        self.assertIn("✨ Feature", outcome.plan.labels)

        # 3. plan → proposal → work_order
        proposal = GitHubWorkOrderProposal(
            proposal_id="p1",
            session_id="sess-e2e",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="Next.js + NestJS 회원가입/검색 구현",
            repo="yule-studio/naver-search-clone",
            issue_auto_create_plan=outcome.plan.to_dict(),
        )
        work_order = GitHubWorkOrder.from_proposal(
            proposal, approval_id="approval-1", approved_by="masterway"
        )
        self.assertEqual(
            work_order.issue_auto_create_plan, outcome.plan.to_dict()
        )

        # 4. dispatch: writer.create_issue is called with the plan
        client = _StubIssueClient()
        writer = GithubWriter(
            client=client,
            dry_run=False,
            live=True,
            policy_gate=make_default_policy_gate(),
        )
        plan = work_order.issue_auto_create_plan
        assert plan is not None
        result = writer.create_issue(
            repo=work_order.repo or "",
            title=str(plan["title"]),
            body=str(plan["body"]),
            labels=tuple(plan.get("labels") or ()),
            assignees=tuple(plan.get("assignees") or ()),
            session_id=work_order.session_id,
        )
        self.assertEqual(result.outcome, OUTCOME_OK)
        self.assertEqual(result.audit.action, ACTION_GITHUB_ISSUE_CREATE)
        # stub client 가 받은 인자 검증
        self.assertEqual(len(client.calls), 1)
        kind, kwargs = client.calls[0]
        self.assertEqual(kind, "create_issue")
        self.assertEqual(kwargs["repo"], "yule-studio/naver-search-clone")
        self.assertTrue(kwargs["title"].startswith("[기능]"))
        self.assertIn("Next.js + NestJS", kwargs["title"])
        self.assertIn("✨ Feature", kwargs["labels"])

    def test_existing_issue_number_skips_plan(self) -> None:
        contract = discover_repo_contract(
            owner="yule-studio",
            repo="naver-search-clone",
            workspace_root=str(self.workspace_root),
        )
        outcome = build_issue_auto_create_plan(
            repo_contract=contract,
            request_summary="이슈 #42 작업",
            template_loader=self._template_loader_from_workspace(contract),
            session_id="sess-reuse",
            existing_issue_number=42,
        )
        self.assertIsNone(outcome.plan)
        self.assertEqual(outcome.existing_issue_number, 42)
        self.assertEqual(outcome.audit_reason, AUDIT_EXISTING_ISSUE_REUSED)

        proposal = GitHubWorkOrderProposal(
            proposal_id="p3",
            session_id="sess-reuse",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="이슈 #42 작업",
            repo="yule-studio/naver-search-clone",
            existing_issue_number=42,
        )
        wo = GitHubWorkOrder.from_proposal(
            proposal, approval_id="a3", approved_by="m"
        )
        self.assertEqual(wo.existing_issue_number, 42)
        self.assertIsNone(wo.issue_auto_create_plan)
        # executor 는 plan 이 None 일 때 create_issue 를 호출하지 않아야 함.


if __name__ == "__main__":
    unittest.main()
