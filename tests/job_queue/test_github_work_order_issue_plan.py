"""GitHubWorkOrderProposal / GitHubWorkOrder 에 issue auto-create plan
필드가 round-trip 되는지 회귀 핀.

contract:
  - proposal 의 ``issue_auto_create_plan`` / ``existing_issue_number``
    가 to_payload/from_payload 무손실 보존.
  - from_proposal 이 work_order 로 plan 을 그대로 전달.
  - work_order 의 to_payload/from_payload 도 무손실.
  - existing_issue_number 가 있으면 plan 은 None — executor 가 issue 생성
    을 건너뛰는 신호.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    GitHubWorkOrderProposal,
)


def _sample_plan() -> dict:
    return {
        "title": "[Feature] 회원가입/검색 기능",
        "body": "## 어떤 기능인가요?\n> 본문\n",
        "labels": ["✨ Feature", "📃 Docs"],
        "assignees": [],
        "template_path": ".github/ISSUE_TEMPLATE/feature.md",
        "confidence": "high",
        "audit_reason": "template_used",
        "needs_operator_decision": False,
        "template_score": 2,
    }


class ProposalRoundTripTests(unittest.TestCase):
    def test_plan_survives_payload_round_trip(self) -> None:
        plan = _sample_plan()
        proposal = GitHubWorkOrderProposal(
            proposal_id="p1",
            session_id="s1",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="full-stack 구현",
            repo="yule-studio/naver-search-clone",
            issue_auto_create_plan=plan,
        )
        restored = GitHubWorkOrderProposal.from_payload(proposal.to_payload())
        self.assertEqual(restored.issue_auto_create_plan, plan)
        self.assertIsNone(restored.existing_issue_number)

    def test_existing_issue_round_trip_keeps_plan_none(self) -> None:
        proposal = GitHubWorkOrderProposal(
            proposal_id="p2",
            session_id="s2",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="issue #42 작업",
            existing_issue_number=42,
        )
        restored = GitHubWorkOrderProposal.from_payload(proposal.to_payload())
        self.assertEqual(restored.existing_issue_number, 42)
        self.assertIsNone(restored.issue_auto_create_plan)


class WorkOrderRoundTripTests(unittest.TestCase):
    def test_from_proposal_carries_plan(self) -> None:
        plan = _sample_plan()
        proposal = GitHubWorkOrderProposal(
            proposal_id="p1",
            session_id="s1",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="x",
            issue_auto_create_plan=plan,
        )
        wo = GitHubWorkOrder.from_proposal(
            proposal, approval_id="a1", approved_by="masterway"
        )
        self.assertEqual(wo.issue_auto_create_plan, plan)
        # payload round-trip
        restored = GitHubWorkOrder.from_payload(wo.to_payload())
        self.assertEqual(restored.issue_auto_create_plan, plan)
        self.assertEqual(restored.approval_id, "a1")

    def test_existing_issue_propagates(self) -> None:
        proposal = GitHubWorkOrderProposal(
            proposal_id="p2",
            session_id="s2",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="reuse issue",
            existing_issue_number=99,
        )
        wo = GitHubWorkOrder.from_proposal(
            proposal, approval_id="a2", approved_by="m"
        )
        self.assertEqual(wo.existing_issue_number, 99)
        self.assertIsNone(wo.issue_auto_create_plan)
        restored = GitHubWorkOrder.from_payload(wo.to_payload())
        self.assertEqual(restored.existing_issue_number, 99)


if __name__ == "__main__":
    unittest.main()
