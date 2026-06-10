"""approval_reply — A-M5a-2 unit tests.

Pin every routing branch:

  * intent parser separates APPROVE / REJECT / HOLD / UNCLEAR
  * resolver finds the matching SAVED approval_post row by source
    message id / source thread id / fall back to most recent
  * approval_to_obsidian_write_request preserves audit fields
    (approval_id, approved_by, approved_at, decision_id) and
    refuses non-Obsidian approval kinds
  * handle_approval_reply enqueues an obsidian_write on APPROVE,
    is idempotent on duplicate replies, records rejection on
    REJECT, no-ops on HOLD/UNCLEAR / no matching approval
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.approval_reply import (
    ApprovalIntent,
    ApprovalReplyOutcome,
    approval_to_obsidian_write_request,
    find_replyable_approval,
    handle_approval_reply,
    parse_approval_intent,
)
from yule_engineering.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    APPROVAL_KIND_RESEARCH_PROMOTION,
    ApprovalRequest,
    ApprovalWorker,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    NOTE_KIND_KNOWLEDGE,
    ObsidianWriterWorker,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Intent parser
# ---------------------------------------------------------------------------


class ApproveIntentTests(unittest.TestCase):
    def test_exact_phrase_matches_approve(self) -> None:
        for text in (
            "승인",
            "이대로 진행",
            "저장 승인",
            "approve",
            "ok",
            "go",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    parse_approval_intent(text), ApprovalIntent.APPROVE
                )

    def test_short_phrase_containing_approve_token_matches(self) -> None:
        # "이대로 진행해 줘" contains "이대로 진행" — short reply, accept.
        self.assertEqual(
            parse_approval_intent("이대로 진행해 줘"),
            ApprovalIntent.APPROVE,
        )

    def test_long_message_with_approve_token_stays_unclear(self) -> None:
        # Long replies that incidentally mention "ok" / "approve"
        # are NOT parsed as approve — too easy to misfire on
        # casual chat.
        self.assertEqual(
            parse_approval_intent(
                "approve 라는 단어 자체에 대해 길게 설명을 좀 하면 좋겠어 "
                "왜냐하면 의미가 사람마다 다르니까"
            ),
            ApprovalIntent.UNCLEAR,
        )


class RejectIntentTests(unittest.TestCase):
    def test_reject_phrases(self) -> None:
        for text in ("반려", "거절", "저장하지 마", "reject", "거부"):
            with self.subTest(text=text):
                self.assertEqual(
                    parse_approval_intent(text), ApprovalIntent.REJECT
                )

    def test_reject_wins_over_approve_when_both_present(self) -> None:
        # Pathological reply — user fixing themselves. Reject wins.
        self.assertEqual(
            parse_approval_intent("저장 승인 반려"),
            ApprovalIntent.REJECT,
        )


class HoldAndUnclearIntentTests(unittest.TestCase):
    def test_hold_phrases(self) -> None:
        for text in ("보류", "잠시 보류", "wait", "hold"):
            with self.subTest(text=text):
                self.assertEqual(
                    parse_approval_intent(text), ApprovalIntent.HOLD
                )

    def test_empty_or_unrelated_text_is_unclear(self) -> None:
        for text in ("", "   ", "음 일단 봐줘", "오 좋네 한번 검토 더 해보자"):
            with self.subTest(text=text):
                self.assertEqual(
                    parse_approval_intent(text), ApprovalIntent.UNCLEAR
                )


# ---------------------------------------------------------------------------
# Resolver — match a reply to its approval_post row
# ---------------------------------------------------------------------------


class _FixtureBase(unittest.TestCase):
    """Shared per-test SQLite + workers."""

    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)
        self.posted: List[tuple] = []

        async def post_fn(request, rendered):
            self.posted.append((request, rendered))
            return {"posted_message_id": 9000}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: 7777,
        )

        self.obsidian_writes: List[tuple] = []

        def render_fn(_request):
            return {"rendered": True}

        def write_fn(note, vault, request):
            self.obsidian_writes.append((note, vault, request))
            return None

        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=render_fn,
            write_fn=write_fn,
            vault_root_resolver=lambda _r: Path(self._tmp.name) / "vault",
        )

    def _seed_saved_obsidian_approval(
        self,
        *,
        session_id: str = "sess-reply-1",
        title: str = "k8s 운영 결정 노트",
        source_thread_id: int = 4242,
        source_message_id: Optional[int] = 5555,
        decision_id: str = "dec-abc",
    ) -> str:
        request = ApprovalRequest(
            session_id=session_id,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title=title,
            summary="3 개 source 검토",
            requested_action="vault decisions 저장",
            created_by="tech-lead",
            source_thread_id=source_thread_id,
            source_message_id=source_message_id,
            extra={"decision_id": decision_id, "policy_level": "L3_HUMAN_REQUIRED"},
        )
        # Run the ApprovalWorker happy path so the row lands SAVED —
        # exactly the state a real reply would target.
        outcome = _run(self.approval_worker.run_one(request))
        assert outcome.job is not None
        return outcome.job.job_id


class ResolverTests(_FixtureBase):
    def test_resolver_matches_by_source_message_id_first(self) -> None:
        self._seed_saved_obsidian_approval(source_message_id=5555)
        self._seed_saved_obsidian_approval(
            title="다른 결정", source_message_id=6666
        )
        match = find_replyable_approval(
            queue=self.queue,
            session_id="sess-reply-1",
            source_message_id=6666,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(
            (match.payload or {}).get("source_message_id"), 6666
        )

    def test_resolver_falls_back_to_thread_match(self) -> None:
        self._seed_saved_obsidian_approval(source_thread_id=4242)
        self._seed_saved_obsidian_approval(
            title="다른 결정", source_thread_id=9999
        )
        # No source_message_id supplied — fallback to thread match.
        match = find_replyable_approval(
            queue=self.queue,
            session_id="sess-reply-1",
            source_message_id=None,
            source_thread_id=9999,
        )
        assert match is not None
        self.assertEqual(
            (match.payload or {}).get("source_thread_id"), 9999
        )

    def test_resolver_returns_none_for_session_with_no_approval(self) -> None:
        match = find_replyable_approval(
            queue=self.queue,
            session_id="sess-no-approval",
            source_message_id=1234,
        )
        self.assertIsNone(match)

    def test_resolver_filters_by_approval_kind(self) -> None:
        # Seed an Obsidian approval AND a research_promotion approval
        # for the same session. With approval_kind filter, the
        # resolver must return the right one.
        self._seed_saved_obsidian_approval()
        # Manually enqueue + drive a research_promotion approval to SAVED.
        rp_request = ApprovalRequest(
            session_id="sess-reply-1",
            approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
            title="hero copy 승격",
            summary="frontend + design 합의",
            requested_action="research → decisions",
            created_by="tech-lead",
        )
        rp = _run(self.approval_worker.run_one(rp_request))
        assert rp.job is not None

        match = find_replyable_approval(
            queue=self.queue,
            session_id="sess-reply-1",
            approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
        )
        assert match is not None
        self.assertEqual(
            (match.payload or {}).get("approval_kind"),
            APPROVAL_KIND_RESEARCH_PROMOTION,
        )


# ---------------------------------------------------------------------------
# Converter — ApprovalRequest → ObsidianWriteRequest
# ---------------------------------------------------------------------------


class ConverterTests(unittest.TestCase):
    def test_obsidian_kind_converts_with_audit_fields(self) -> None:
        request = ApprovalRequest(
            session_id="sess-1",
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="x",
            requested_action="vault 저장",
            created_by="tech-lead",
            source_thread_id=2002,
            source_message_id=3003,
            extra={"decision_id": "dec-1", "policy_level": "L3_HUMAN_REQUIRED"},
        )
        write = approval_to_obsidian_write_request(
            approval_request=request,
            approval_id="apv-job-1",
            approved_by="masterway",
            approved_at="2026-05-07T13:00:00+00:00",
            source_message_id=4004,
        )
        self.assertEqual(write.session_id, "sess-1")
        self.assertEqual(write.note_kind, NOTE_KIND_KNOWLEDGE)
        self.assertEqual(write.title, "결정 노트")
        self.assertEqual(write.approval_id, "apv-job-1")
        self.assertEqual(write.approved_by, "masterway")
        self.assertEqual(write.approved_at, "2026-05-07T13:00:00+00:00")
        self.assertEqual(write.source_thread_id, 2002)
        # Decision metadata flows into write.metadata so the audit
        # trail survives the queue trip.
        self.assertEqual(write.metadata.get("decision_id"), "dec-1")
        self.assertEqual(
            write.metadata.get("policy_level"), "L3_HUMAN_REQUIRED"
        )

    def test_non_obsidian_kind_refuses_conversion(self) -> None:
        request = ApprovalRequest(
            session_id="sess-1",
            approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
            title="x",
            summary="x",
            requested_action="x",
            created_by="x",
        )
        with self.assertRaises(ValueError):
            approval_to_obsidian_write_request(
                approval_request=request,
                approval_id="apv-1",
                approved_by="masterway",
            )


# ---------------------------------------------------------------------------
# handle_approval_reply — the main router
# ---------------------------------------------------------------------------


class HandleApprovalReplyTests(_FixtureBase):
    def test_approve_enqueues_obsidian_write_with_audit(self) -> None:
        approval_job_id = self._seed_saved_obsidian_approval(
            decision_id="dec-1"
        )
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="이대로 저장",
            session_id="sess-reply-1",
            approved_by="masterway",
            source_message_id=8888,
            source_thread_id=4242,
            approved_at="2026-05-07T13:00:00+00:00",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.APPROVE)
        self.assertEqual(outcome.approval_job_id, approval_job_id)
        self.assertIsNotNone(outcome.write_job_id)
        self.assertIsNone(outcome.skipped_reason)
        # Audit fields flow into the outcome so the gateway can log
        # "사용자 X 가 Y 시에 카드 Z 을 승인" without re-reading the queue.
        self.assertEqual(outcome.audit.get("approved_by"), "masterway")
        self.assertEqual(
            outcome.audit.get("approved_at"), "2026-05-07T13:00:00+00:00"
        )
        self.assertEqual(outcome.audit.get("decision_id"), "dec-1")

        # The actual queue row must carry the same approval triple
        # so ObsidianWriterWorker's guard sees a valid approval.
        rows = self.queue.list_for_session(
            "sess-reply-1", states=[JobState.QUEUED]
        )
        write_rows = [
            r for r in rows if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)
        payload = write_rows[0].payload or {}
        self.assertEqual(payload.get("approval_id"), approval_job_id)
        self.assertEqual(payload.get("approved_by"), "masterway")
        self.assertEqual(
            payload.get("approved_at"), "2026-05-07T13:00:00+00:00"
        )

    def test_duplicate_approve_does_not_enqueue_twice(self) -> None:
        self._seed_saved_obsidian_approval()

        first = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id="sess-reply-1",
            approved_by="masterway",
        )
        second = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id="sess-reply-1",
            approved_by="masterway",
        )
        self.assertIsNone(first.skipped_reason)
        # Second call still recognises APPROVE intent and matches
        # the same approval_job, but ObsidianWriterWorker.enqueue's
        # idempotency drops the duplicate write — surfaced as
        # ``duplicate_obsidian_write`` skipped_reason so the gateway
        # can render "이미 저장 큐에 들어가 있어요" instead of "OK".
        self.assertEqual(
            second.skipped_reason, "duplicate_obsidian_write"
        )
        # Queue carries exactly one obsidian_write row.
        write_rows = [
            r for r in self.queue.list_for_session("sess-reply-1")
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)

    def test_reject_records_rejection_without_enqueueing_write(self) -> None:
        # Persistence_fn injection lets us avoid touching the real
        # workflow store from a queue-only test.
        captured: List[dict] = []

        def fake_persist(**kwargs):
            captured.append(kwargs)

        self._seed_saved_obsidian_approval(decision_id="dec-1")
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="저장하지 마",
            session_id="sess-reply-1",
            approved_by="masterway",
            persist_rejection_fn=fake_persist,
            approved_at="2026-05-07T13:30:00+00:00",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.REJECT)
        self.assertTrue(outcome.rejection_recorded)
        # No obsidian_write row.
        write_rows = [
            r for r in self.queue.list_for_session("sess-reply-1")
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(write_rows, [])
        # Persistence stub got the audit fields.
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["rejected_by"], "masterway")
        self.assertEqual(
            captured[0]["rejected_at"], "2026-05-07T13:30:00+00:00"
        )
        self.assertEqual(captured[0]["reason"], "저장하지 마")
        # Outcome's audit reflects the matched approval's decision_id.
        self.assertEqual(outcome.audit.get("decision_id"), "dec-1")

    def test_hold_intent_is_no_op(self) -> None:
        self._seed_saved_obsidian_approval()
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="잠시 보류",
            session_id="sess-reply-1",
            approved_by="masterway",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.HOLD)
        self.assertEqual(
            outcome.skipped_reason, "intent_not_actionable"
        )
        self.assertIsNone(outcome.write_job_id)
        self.assertIsNone(outcome.approval_job_id)

    def test_unclear_intent_is_no_op(self) -> None:
        self._seed_saved_obsidian_approval()
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="음 좀 더 보고 결정할게요",
            session_id="sess-reply-1",
            approved_by="masterway",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.UNCLEAR)
        self.assertEqual(
            outcome.skipped_reason, "intent_not_actionable"
        )

    def test_no_matching_approval_returns_skipped(self) -> None:
        # session has no SAVED approval_post row — APPROVE intent
        # is recognised but skipped with "no_matching_approval".
        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id="sess-with-no-pending",
            approved_by="masterway",
        )
        self.assertEqual(outcome.intent, ApprovalIntent.APPROVE)
        self.assertEqual(
            outcome.skipped_reason, "no_matching_approval"
        )
        self.assertIsNone(outcome.write_job_id)

    def test_non_obsidian_approval_kind_is_skipped(self) -> None:
        # research_promotion approvals don't have an obsidian write
        # downstream (yet). Resolver matches → converter refuses →
        # outcome carries skipped_reason="approval_kind_not_handled"
        # so the gateway can render an informative message.
        from yule_engineering.agents.job_queue.approval_worker import (
            APPROVAL_KIND_RESEARCH_PROMOTION,
            ApprovalRequest as _AR,
        )

        rp = _run(
            self.approval_worker.run_one(
                _AR(
                    session_id="sess-rp-1",
                    approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
                    title="research promotion",
                    summary="x",
                    requested_action="x",
                    created_by="tech-lead",
                )
            )
        )
        assert rp.job is not None

        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="승인",
            session_id="sess-rp-1",
            approved_by="masterway",
            approval_kind=APPROVAL_KIND_RESEARCH_PROMOTION,
        )
        self.assertEqual(outcome.intent, ApprovalIntent.APPROVE)
        self.assertEqual(outcome.approval_job_id, rp.job.job_id)
        self.assertEqual(
            outcome.skipped_reason, "approval_kind_not_handled"
        )
        self.assertIsNone(outcome.write_job_id)


if __name__ == "__main__":
    unittest.main()
