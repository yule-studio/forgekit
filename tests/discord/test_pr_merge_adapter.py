"""F16 PR-2 — Discord PR merge adapter tests (issue #128).

The adapter is the seam between a producer (which builds a
:class:`PRMergeProposal`) and the :class:`ApprovalWorker` that posts
the card. These tests use a real on-disk SQLite queue + a fake
``post_fn`` so we can assert end-to-end behaviour without GitHub or
Discord.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    ApprovalRequest,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeProposal,
)
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.discord.pr_merge_adapter import (
    PRMergeApprovalOutcome,
    SKIPPED_DUPLICATE_PR_MERGE_CARD,
    SKIPPED_NO_PROPOSAL,
    enqueue_pr_merge_approval,
)


def _proposal(**overrides) -> PRMergeProposal:
    base = dict(
        repo="yule-studio/yule-studio-agent",
        pr_number=127,
        pr_title="F15 corporate structure",
        pr_url="https://github.com/yule-studio/yule-studio-agent/pull/127",
        head_sha="abc1234567",
        base_branch="main",
        draft=False,
        mergeable_state="clean",
        summary_md="",
        scope_labels=("docs", "agents"),
        risk="LOW",
        check_runs_summary="✅ Tests: 18/18 PASS",
        branch_protection_summary="🔒 reviews 1/1",
        body_excerpt="adds 6 dept + 19 roles",
        requested_by="alice",
    )
    base.update(overrides)
    return PRMergeProposal(**base)


class _AdapterFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.posted: list[tuple[ApprovalRequest, str]] = []

        async def post_fn(request, rendered):
            self.posted.append((request, rendered))
            return {"posted_message_id": 12345}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: 4242,
        )
        self.session = SimpleNamespace(session_id="sess-pr-127", extra={})


class EnqueueHappyPathTests(_AdapterFixture):
    async def test_enqueue_drives_consumer_and_posts(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),
            approval_worker=self.approval_worker,
            source_channel_id=999,
            source_thread_id=1001,
            source_message_id=2002,
        )
        self.assertIsInstance(outcome, PRMergeApprovalOutcome)
        self.assertIsNotNone(outcome.proposal)
        self.assertIsNone(outcome.skipped_reason)
        self.assertIsNotNone(outcome.approval_job_id)
        # post_fn was called once with the rendered card.
        self.assertEqual(len(self.posted), 1)
        request, rendered = self.posted[0]
        self.assertEqual(request.approval_kind, APPROVAL_KIND_PR_MERGE)
        self.assertIn("PR 머지 승인 — #127", request.title)
        self.assertIn("F15 corporate structure", request.title)
        self.assertIn("위험도: LOW", rendered)
        self.assertIn(
            "https://github.com/yule-studio/yule-studio-agent/pull/127",
            rendered,
        )

    async def test_summary_body_is_the_rendered_card(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),
            approval_worker=self.approval_worker,
        )
        # ApprovalRequest.summary carries the full rendered card so the
        # worker can re-use it without a second render pass.
        request, _ = self.posted[0]
        self.assertIn("응답 어휘", request.summary)
        self.assertIn("승인 / 거절 / 수정 후 다시 / 머지 보류", request.summary)

    async def test_proposal_extra_round_trips_through_request(self) -> None:
        await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),
            approval_worker=self.approval_worker,
        )
        request, _ = self.posted[0]
        self.assertEqual(request.extra.get("repo"), "yule-studio/yule-studio-agent")
        self.assertEqual(request.extra.get("pr_number"), 127)
        self.assertEqual(request.extra.get("head_sha"), "abc1234567")
        self.assertEqual(request.extra.get("risk"), "LOW")


class EnqueueDedupTests(_AdapterFixture):
    async def test_duplicate_same_head_sha_skipped(self) -> None:
        first = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),
            approval_worker=self.approval_worker,
            source_message_id=2002,
        )
        self.assertIsNone(first.skipped_reason)
        self.assertEqual(len(self.posted), 1)

        second = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),  # same head_sha
            approval_worker=self.approval_worker,
            source_message_id=2002,
        )
        self.assertEqual(
            second.skipped_reason, SKIPPED_DUPLICATE_PR_MERGE_CARD
        )
        # No second post.
        self.assertEqual(len(self.posted), 1)

    async def test_new_head_sha_does_not_dedup(self) -> None:
        first = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(head_sha="aaa0000000"),
            approval_worker=self.approval_worker,
            source_message_id=2002,
        )
        self.assertIsNone(first.skipped_reason)

        # Same session, same source_message_id, but different head_sha —
        # this is a follow-up commit and deserves a fresh card.
        second = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(head_sha="bbb1111111"),
            approval_worker=self.approval_worker,
            source_message_id=2002,
        )
        # We don't dedup — a new card should post.
        self.assertIsNone(second.skipped_reason)
        self.assertEqual(len(self.posted), 2)


class EnqueueDefensiveTests(_AdapterFixture):
    async def test_missing_proposal_skipped(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=None,
            approval_worker=self.approval_worker,
        )
        self.assertEqual(outcome.skipped_reason, SKIPPED_NO_PROPOSAL)
        self.assertEqual(len(self.posted), 0)

    async def test_missing_session_id_skipped(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session=SimpleNamespace(session_id="", extra={}),
            proposal=_proposal(),
            approval_worker=self.approval_worker,
        )
        self.assertEqual(outcome.skipped_reason, "session_id_missing")

    async def test_dict_session_resolves(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session={"session_id": "sess-from-dict"},
            proposal=_proposal(),
            approval_worker=self.approval_worker,
        )
        self.assertIsNone(outcome.skipped_reason)
        request, _ = self.posted[0]
        self.assertEqual(request.session_id, "sess-from-dict")


class EnqueueWithoutDrivingConsumerTests(_AdapterFixture):
    async def test_drive_consumer_false_queues_only(self) -> None:
        outcome = await enqueue_pr_merge_approval(
            session=self.session,
            proposal=_proposal(),
            approval_worker=self.approval_worker,
            drive_consumer=False,
        )
        self.assertIsNotNone(outcome.approval_job_id)
        # post_fn was NOT called.
        self.assertEqual(len(self.posted), 0)


if __name__ == "__main__":
    unittest.main()
