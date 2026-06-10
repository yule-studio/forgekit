"""F16 PR-2 — handle_pr_merge_approval_reply tests (issue #128).

The handler is the seam between Discord (user typed "승인") and the
merge executor (which is wired in commit 10). These tests use a fake
queue + a fake executor so every branch can be pinned without
GitHub or live ApprovalWorker.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeProposal,
    PRMergeReplyDispatch,
    PRMergeReplyIntent,
    PRMergeReplyResult,
    handle_pr_merge_approval_reply,
)
from yule_engineering.agents.job_queue.store import JobQueue


def _proposal(**overrides) -> PRMergeProposal:
    base = dict(
        repo="yule-studio/yule-studio-agent",
        pr_number=127,
        pr_title="F15",
        pr_url="https://github.com/yule-studio/yule-studio-agent/pull/127",
        head_sha="abc1234567",
        base_branch="main",
        draft=False,
        mergeable_state="clean",
        summary_md="",
        scope_labels=("docs",),
        risk="LOW",
        check_runs_summary="✅ green",
        branch_protection_summary="🔒 ok",
        body_excerpt="x",
        requested_by="alice",
    )
    base.update(overrides)
    return PRMergeProposal(**base)


class _Fixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.posted: list = []

        async def post_fn(request, rendered):
            self.posted.append((request, rendered))
            return {"posted_message_id": 12345}

        self.worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: 4242,
        )

    async def _seed_pr_merge_card(
        self, *, session_id: str = "sess-pr", source_message_id: int = 2002
    ) -> None:
        """Enqueue a PR_MERGE approval card so the handler can find it."""

        from yule_discord.integrations.pr_merge_adapter import (
            enqueue_pr_merge_approval,
        )
        from types import SimpleNamespace

        await enqueue_pr_merge_approval(
            session=SimpleNamespace(session_id=session_id, extra={}),
            proposal=_proposal(),
            approval_worker=self.worker,
            source_channel_id=999,
            source_thread_id=1001,
            source_message_id=source_message_id,
        )


class HandleReplyIntentTests(_Fixture):
    async def test_hold_short_circuits(self) -> None:
        await self._seed_pr_merge_card()
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="보류",
            session_id="sess-pr",
            approved_by="alice",
            source_message_id=999,
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.HOLD)
        self.assertEqual(result.skipped_reason, "intent_not_actionable")

    async def test_unclear_short_circuits(self) -> None:
        await self._seed_pr_merge_card()
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="음 뭐였더라",
            session_id="sess-pr",
            approved_by="alice",
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.UNCLEAR)

    async def test_no_matching_card(self) -> None:
        # No card seeded.
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="alice",
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.APPROVE)
        self.assertEqual(result.skipped_reason, "no_matching_approval")


class HandleRejectTests(_Fixture):
    async def test_reject_records_audit(self) -> None:
        await self._seed_pr_merge_card()
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="거절",
            session_id="sess-pr",
            approved_by="alice",
            approved_at="2026-05-13T08:00:00Z",
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.REJECT)
        self.assertTrue(result.rejection_recorded)
        self.assertEqual(result.audit.get("rejected_by"), "alice")
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.proposal.pr_number, 127)


class HandleReviseTests(_Fixture):
    async def test_revise_intent_records_audit(self) -> None:
        await self._seed_pr_merge_card()
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="수정 후 다시",
            session_id="sess-pr",
            approved_by="alice",
            approved_at="2026-05-13T08:00:00Z",
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.REVISE_AND_REPEAT)
        self.assertEqual(result.audit.get("revise_requested_by"), "alice")
        # No merge attempted.
        self.assertFalse(result.merge_disabled)
        self.assertIsNone(result.merge_result)


class HandleApproveTests(_Fixture):
    async def test_approve_without_executor_returns_merge_disabled(self) -> None:
        await self._seed_pr_merge_card()
        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="alice",
            approved_at="2026-05-13T08:00:00Z",
            merge_executor=None,
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.APPROVE)
        self.assertTrue(result.merge_disabled)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(result.audit.get("reason"), "merge_executor_not_registered")

    async def test_approve_calls_sync_executor(self) -> None:
        await self._seed_pr_merge_card()
        called: list = []

        def executor(dispatch: PRMergeReplyDispatch):
            called.append(dispatch)
            return {"merge_sha": "def9876543", "method": "squash"}

        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="alice",
            merge_executor=executor,
        )
        self.assertEqual(result.intent, PRMergeReplyIntent.APPROVE)
        self.assertEqual(len(called), 1)
        dispatch = called[0]
        self.assertEqual(dispatch.proposal.pr_number, 127)
        self.assertEqual(dispatch.approved_by, "alice")
        self.assertFalse(result.merge_disabled)
        self.assertIsNotNone(result.merge_result)
        self.assertEqual(result.merge_result["merge_sha"], "def9876543")

    async def test_approve_calls_async_executor(self) -> None:
        await self._seed_pr_merge_card()
        called: list = []

        async def executor(dispatch: PRMergeReplyDispatch):
            called.append(dispatch)
            await asyncio.sleep(0)
            return {"merge_sha": "ff00112233"}

        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="bob",
            merge_executor=executor,
        )
        self.assertEqual(len(called), 1)
        self.assertEqual(result.merge_result["merge_sha"], "ff00112233")

    async def test_approve_executor_returns_gate_failure(self) -> None:
        await self._seed_pr_merge_card()

        def executor(dispatch: PRMergeReplyDispatch):
            return {
                "gate_failed_step": "checks_green",
                "gate_reason": "1 check failed",
            }

        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="alice",
            merge_executor=executor,
        )
        self.assertEqual(result.gate_failed_step, "checks_green")
        self.assertIn("1 check failed", result.gate_reason)
        self.assertIsNone(result.merge_result)


class HandleWrongKindTests(_Fixture):
    async def test_ignores_obsidian_card(self) -> None:
        # Seed an OBSIDIAN_WRITE card instead.
        request = ApprovalRequest(
            session_id="sess-pr",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="t",
            summary="s",
            requested_action="save",
            created_by="alice",
            source_thread_id=1001,
        )
        await self.worker.run_one(request)

        result = await handle_pr_merge_approval_reply(
            queue=self.queue,
            text="승인",
            session_id="sess-pr",
            approved_by="alice",
        )
        # No PR_MERGE card → no matching approval (filter excludes
        # OBSIDIAN_WRITE).
        self.assertEqual(result.skipped_reason, "no_matching_approval")


if __name__ == "__main__":
    unittest.main()
