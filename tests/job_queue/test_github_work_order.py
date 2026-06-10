"""G4 — github_work_order queue layer tests.

Pins:

  * Coding-intent detector recognises bug fixes / PR / 이슈 / 구현 /
    테스트 추가 / 리팩터 / GitHub Actions verbs and exposes the action
    label so the approval card can render the right "요청 액션" line.
  * Research-only phrasing vetoes a positive coding match (a user who
    types "코드 수정 없이 PR 올려줘" stays in research-only mode).
  * GitHubWorkOrderProposal / GitHubWorkOrder round-trip through
    to_payload / from_payload without losing fields.
  * dispatch_github_work_order refuses to enqueue without an approval
    triple (the load-bearing "no GitHub write before approval" guard).
  * Idempotent dispatch — same proposal_id / source_message_id never
    produces two queue rows.
  * GitHubWorkOrder.from_proposal defaults dry_run=True; explicit
    operator override flips it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.github_work_order import (
    APPROVAL_KIND_GITHUB_WORK_ORDER,
    JOB_TYPE_GITHUB_WORK_ORDER,
    SKIPPED_AWAITING_APPROVAL,
    SKIPPED_DUPLICATE,
    CodingIntent,
    GitHubWorkOrder,
    GitHubWorkOrderProposal,
    detect_coding_intent,
    dispatch_github_work_order,
    find_active_work_order,
)
from yule_engineering.agents.job_queue.store import JobQueue


class _QueueFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue = JobQueue(db_path=Path(self._tmp.name) / "queue.sqlite3")


# ---------------------------------------------------------------------------
# Coding intent detector
# ---------------------------------------------------------------------------


class CodingIntentDetectorTests(unittest.TestCase):
    def test_simple_research_request_is_not_coding(self) -> None:
        ci = detect_coding_intent("DevOps 엔지니어가 되려면 어떤 책을 읽어야 해?")
        self.assertFalse(ci.coding_required)
        self.assertEqual(ci.matched, ())
        self.assertEqual(ci.actions, ())
        self.assertFalse(ci.research_only)

    def test_pr_with_bug_fix_classified_as_coding(self) -> None:
        ci = detect_coding_intent("이 버그 고쳐서 PR 올려줘")
        self.assertTrue(ci.coding_required)
        self.assertIn("bug_fix", ci.actions)
        self.assertIn("pull_request", ci.actions)
        self.assertTrue(any("pr" in m for m in ci.matched))

    def test_github_actions_workflow_request(self) -> None:
        ci = detect_coding_intent("GitHub Actions workflow 고쳐줘")
        self.assertTrue(ci.coding_required)
        self.assertIn("github_actions", ci.actions)

    def test_test_addition_request(self) -> None:
        ci = detect_coding_intent("이 모듈에 단위 테스트 추가해줘")
        self.assertTrue(ci.coding_required)
        self.assertIn("test_add", ci.actions)

    def test_refactor_request(self) -> None:
        ci = detect_coding_intent("이 클래스 리팩터해줘")
        self.assertTrue(ci.coding_required)
        self.assertIn("refactor", ci.actions)

    def test_research_only_phrase_vetoes_positive_match(self) -> None:
        # Even with a positive verb ("PR 올려줘"), the explicit
        # research-only phrase wins.
        ci = detect_coding_intent("코드 수정 없이 PR 올려줘")
        self.assertFalse(ci.coding_required)
        self.assertTrue(ci.research_only)
        self.assertEqual(ci.matched, ())

    def test_empty_text_is_neutral(self) -> None:
        ci = detect_coding_intent("")
        self.assertFalse(ci.coding_required)
        self.assertFalse(ci.research_only)


# ---------------------------------------------------------------------------
# Proposal / work order dataclasses
# ---------------------------------------------------------------------------


class ProposalRoundTripTests(unittest.TestCase):
    def test_to_payload_then_from_payload_preserves_fields(self) -> None:
        original = GitHubWorkOrderProposal(
            proposal_id="gho-abc",
            session_id="sess-1",
            source_channel_id=10,
            source_thread_id=20,
            source_message_id=30,
            request_summary="버그 수정 + PR",
            coding_required=True,
            selected_roles=("tech-lead", "backend-engineer"),
            excluded_roles=("frontend-engineer",),
            intent_actions=("bug_fix", "pull_request"),
            intent_evidence=("버그 고쳐", "pr 올려"),
            approval_kind=APPROVAL_KIND_GITHUB_WORK_ORDER,
            approval_level="L3_HUMAN_APPROVAL",
            repo="acme/app",
            base_branch="main",
            requested_by="masterway",
            dry_run_default=True,
            extra={"hint": "auth flow"},
            created_at="2026-05-08T10:00:00+00:00",
        )
        payload = original.to_payload()
        # JSON-friendly check: top-level types are dict / list / scalar.
        self.assertIsInstance(payload, dict)
        self.assertIsInstance(payload["selected_roles"], list)

        reconstructed = GitHubWorkOrderProposal.from_payload(payload)
        self.assertEqual(reconstructed, original)

    def test_dry_run_default_is_true(self) -> None:
        proposal = GitHubWorkOrderProposal(
            proposal_id="gho-1",
            session_id="s",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="hi",
        )
        self.assertTrue(proposal.dry_run_default)


class WorkOrderRoundTripTests(unittest.TestCase):
    def test_from_proposal_defaults_dry_run_true(self) -> None:
        proposal = GitHubWorkOrderProposal(
            proposal_id="gho-1",
            session_id="s",
            source_channel_id=1,
            source_thread_id=2,
            source_message_id=3,
            request_summary="버그 수정",
            selected_roles=("tech-lead",),
            intent_actions=("bug_fix",),
        )
        wo = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="apv-1",
            approved_by="masterway",
        )
        self.assertTrue(wo.dry_run)
        self.assertEqual(wo.approval_id, "apv-1")
        self.assertEqual(wo.approved_by, "masterway")
        self.assertTrue(wo.approved_at)  # auto-stamped

    def test_explicit_dry_run_false_overrides(self) -> None:
        proposal = GitHubWorkOrderProposal(
            proposal_id="gho-2",
            session_id="s",
            source_channel_id=None,
            source_thread_id=None,
            source_message_id=None,
            request_summary="x",
        )
        wo = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="apv-2",
            approved_by="op",
            dry_run=False,
        )
        self.assertFalse(wo.dry_run)


# ---------------------------------------------------------------------------
# Dispatch / dedup
# ---------------------------------------------------------------------------


class DispatchApprovalGuardTests(_QueueFixture):
    def _proposal(self, **kwargs) -> GitHubWorkOrderProposal:
        defaults = dict(
            proposal_id="gho-guard",
            session_id="sess-guard",
            source_channel_id=1,
            source_thread_id=2,
            source_message_id=42,
            request_summary="버그 수정",
            selected_roles=("tech-lead", "backend-engineer"),
            intent_actions=("bug_fix",),
        )
        defaults.update(kwargs)
        return GitHubWorkOrderProposal(**defaults)

    def test_dispatch_without_approval_id_blocked(self) -> None:
        proposal = self._proposal()
        wo = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="",  # missing approval triple
            approved_by="masterway",
        )
        outcome = dispatch_github_work_order(self.queue, wo)
        self.assertEqual(outcome.skipped_reason, SKIPPED_AWAITING_APPROVAL)
        self.assertIsNone(outcome.job)
        # No queue row was inserted.
        rows = list(self.queue.list_for_session("sess-guard"))
        self.assertEqual(rows, [])

    def test_dispatch_with_approval_id_succeeds(self) -> None:
        proposal = self._proposal()
        wo = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="apv-1",
            approved_by="masterway",
        )
        outcome = dispatch_github_work_order(self.queue, wo)
        self.assertIsNone(outcome.skipped_reason)
        self.assertIsNotNone(outcome.job)
        self.assertEqual(outcome.job.job_type, JOB_TYPE_GITHUB_WORK_ORDER)
        # Payload round-trip via the queue store.
        rows = list(self.queue.list_for_session("sess-guard"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payload["approval_id"], "apv-1")
        self.assertTrue(rows[0].payload["dry_run"])

    def test_dispatch_dedupes_on_proposal_id(self) -> None:
        proposal = self._proposal()
        wo = GitHubWorkOrder.from_proposal(
            proposal,
            approval_id="apv-1",
            approved_by="masterway",
        )
        first = dispatch_github_work_order(self.queue, wo)
        second = dispatch_github_work_order(self.queue, wo)
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(second.skipped_reason, SKIPPED_DUPLICATE)
        rows = list(self.queue.list_for_session("sess-guard"))
        self.assertEqual(len(rows), 1)

    def test_dispatch_dedupes_on_source_message_id(self) -> None:
        wo_a = GitHubWorkOrder.from_proposal(
            self._proposal(proposal_id="gho-a"),
            approval_id="apv-1",
            approved_by="op",
        )
        wo_b = GitHubWorkOrder.from_proposal(
            self._proposal(proposal_id="gho-b"),
            approval_id="apv-2",
            approved_by="op",
        )
        first = dispatch_github_work_order(self.queue, wo_a)
        second = dispatch_github_work_order(self.queue, wo_b)
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(second.skipped_reason, SKIPPED_DUPLICATE)


class FindActiveWorkOrderTests(_QueueFixture):
    def test_returns_none_when_session_empty(self) -> None:
        self.assertIsNone(
            find_active_work_order(self.queue, session_id="missing")
        )

    def test_finds_by_proposal_id(self) -> None:
        wo = GitHubWorkOrder.from_proposal(
            GitHubWorkOrderProposal(
                proposal_id="gho-find",
                session_id="sess-find",
                source_channel_id=None,
                source_thread_id=None,
                source_message_id=None,
                request_summary="x",
            ),
            approval_id="apv",
            approved_by="op",
        )
        dispatch_github_work_order(self.queue, wo)
        match = find_active_work_order(
            self.queue, session_id="sess-find", proposal_id="gho-find"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.payload["proposal_id"], "gho-find")


if __name__ == "__main__":
    unittest.main()
