"""forum_obsidian_handoff — A-M7.5 producer tests.

Pin the missing producer wiring: a forum-thread message saying
"Obsidian 에 정리하고 싶어" must enqueue a #승인-대기 card via
:class:`ApprovalWorker`. Without this producer the user complaint
"thread 에서 저장 요청해도 카드 안 생김" stayed open until A-M7.5.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalWorker,
)
from yule_orchestrator.agents.job_queue.forum_obsidian_handoff import (
    RESPONSE_APPROVAL_CHANNEL_UNSET,
    RESPONSE_APPROVAL_DUPLICATE,
    RESPONSE_APPROVAL_QUEUED,
    RESPONSE_NO_SESSION_FOR_THREAD,
    SKIPPED_APPROVAL_CHANNEL_UNSET,
    SKIPPED_DUPLICATE_APPROVAL,
    SKIPPED_NO_SESSION_FOR_THREAD,
    SKIPPED_NOT_SAVE_REQUEST,
    render_handoff_response,
    route_forum_obsidian_save_request,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _forum_thread_message(
    *,
    channel_id: int = 50001,
    parent_channel_id: int = 50000,
    channel_name: str = "k8s 운영 자료",
    content: str = "Obsidian에 정리하고 싶어",
    author_name: str = "masterway",
    author_id: int = 7,
    message_id: int = 60001,
    guild_id: int = 40000,
):
    channel = SimpleNamespace(
        id=channel_id,
        parent_id=parent_channel_id,
        parent=SimpleNamespace(id=parent_channel_id, name="운영-리서치"),
        name=channel_name,
        guild=SimpleNamespace(id=guild_id),
    )
    author = SimpleNamespace(id=author_id, name=author_name, global_name=author_name)
    return SimpleNamespace(
        id=message_id,
        channel=channel,
        author=author,
        content=content,
        guild=SimpleNamespace(id=guild_id),
        jump_url=f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
    )


def _open_session(
    *,
    session_id: str = "sess-forum-handoff-1",
    forum_thread_id: int = 50001,
    prompt: str = "k8s 운영 자료 정리",
    extra_overrides=None,
):
    extra = {"research_forum_thread_id": forum_thread_id}
    if extra_overrides:
        extra.update(extra_overrides)
    when = datetime.now(tz=timezone.utc)
    return SimpleNamespace(
        session_id=session_id,
        prompt=prompt,
        thread_id=None,
        extra=extra,
        updated_at=when.isoformat(),
        role_sequence=("tech-lead", "devops-engineer"),
    )


class _HandoffFixture(unittest.TestCase):
    APPROVAL_CHANNEL_ID: int = 80001

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        self.posted_cards: List = []

        async def post_fn(request, rendered_text):
            self.posted_cards.append((request, rendered_text))
            return {
                "posted_message_id": 90000 + len(self.posted_cards),
                "channel_id": self.APPROVAL_CHANNEL_ID,
            }

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: self.APPROVAL_CHANNEL_ID,
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class HappyPathTests(_HandoffFixture):
    def test_forum_save_request_enqueues_approval_card(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertIsNotNone(outcome.approval_job_id)
        self.assertIsNone(outcome.skipped_reason)
        # ApprovalWorker actually posted the card via the stub.
        self.assertEqual(len(self.posted_cards), 1)
        request, rendered = self.posted_cards[0]
        self.assertEqual(request.session_id, session.session_id)
        self.assertEqual(request.approval_kind, APPROVAL_KIND_OBSIDIAN_WRITE)
        # Source thread metadata flows through.
        self.assertEqual(request.source_thread_id, message.channel.id)
        self.assertEqual(request.source_message_id, message.id)
        self.assertEqual(request.extra["origin"], "research_forum_save_request")
        self.assertIn("source_thread_url", request.extra)
        self.assertEqual(request.extra["source_thread_title"], message.channel.name)

    def test_response_template_contains_job_id(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        text = render_handoff_response(outcome)
        self.assertIsNotNone(text)
        self.assertIn(outcome.approval_job_id, text)
        self.assertIn("Obsidian 저장 요청", text)


# ---------------------------------------------------------------------------
# Idempotency — same forum-thread message must NOT enqueue twice
# ---------------------------------------------------------------------------


class IdempotencyTests(_HandoffFixture):
    def test_same_message_twice_yields_duplicate_outcome(self) -> None:
        # A-M7.6 — topic-level dedup fires before message-level
        # dedup. Same forum message hitting again finds the topic
        # already pending approval and returns the new
        # SKIPPED_TOPIC_PENDING_APPROVAL outcome (which carries the
        # existing approval job id for navigation). M7.5 message-id
        # dedup remains as a final safety net for sessions where
        # ledger persistence didn't take.
        from yule_orchestrator.agents.job_queue.forum_obsidian_handoff import (
            SKIPPED_TOPIC_PENDING_APPROVAL,
        )

        session = _open_session()
        message = _forum_thread_message()
        first = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        second = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNone(first.skipped_reason)
        # Either dedup signal acceptable — both prove no double-post.
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        # Card was posted exactly once regardless of which dedup fired.
        self.assertEqual(len(self.posted_cards), 1)


# ---------------------------------------------------------------------------
# Negative paths — not a save request, not a forum thread, no session
# ---------------------------------------------------------------------------


class NotSaveRequestTests(_HandoffFixture):
    def test_unrelated_message_passes_through(self) -> None:
        message = _forum_thread_message(content="이 자료 어떻게 봐?")
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
            )
        )
        self.assertFalse(outcome.handled)
        self.assertEqual(outcome.skipped_reason, SKIPPED_NOT_SAVE_REQUEST)
        self.assertEqual(self.posted_cards, [])

    def test_save_request_in_regular_channel_passes_through(self) -> None:
        # parent_id None → not a forum thread; producer must not run.
        channel = SimpleNamespace(id=42, parent_id=None, parent=None, name="general")
        author = SimpleNamespace(id=1, name="m", global_name="m")
        message = SimpleNamespace(
            id=99, channel=channel, author=author, content="Obsidian에 정리해줘"
        )
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session()],
            )
        )
        self.assertFalse(outcome.handled)
        self.assertEqual(self.posted_cards, [])


class NoSessionForThreadTests(_HandoffFixture):
    def test_friendly_no_op_when_session_lookup_fails(self) -> None:
        # Forum thread msg lands but no session has matching
        # research_forum_thread_id → friendly "couldn't find session".
        message = _forum_thread_message(channel_id=99999)
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [_open_session(forum_thread_id=11)],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_NO_SESSION_FOR_THREAD
        )
        self.assertEqual(
            render_handoff_response(outcome), RESPONSE_NO_SESSION_FOR_THREAD
        )
        self.assertEqual(self.posted_cards, [])


# ---------------------------------------------------------------------------
# Approval channel unset — graceful surface
# ---------------------------------------------------------------------------


class ApprovalChannelUnsetTests(_HandoffFixture):
    def test_channel_unset_routes_to_friendly_warning(self) -> None:
        # Override the approval worker so its channel resolver returns None.
        async def _post_fn(_request, _rendered):
            return {"posted_message_id": 1}

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: None,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertTrue(outcome.handled)
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        self.assertEqual(
            render_handoff_response(outcome),
            RESPONSE_APPROVAL_CHANNEL_UNSET,
        )


# ---------------------------------------------------------------------------
# Token-leak guard — error rendering must never echo the token
# ---------------------------------------------------------------------------


class TokenLeakGuardTests(_HandoffFixture):
    def test_worker_exception_message_does_not_leak_secrets(self) -> None:
        async def boom_post(_request, _rendered):
            # Intentional sentinel error string that includes a fake
            # token-shaped payload — the producer must NOT propagate
            # that into the response template.
            raise RuntimeError("status=401 token=secret-do-not-leak")

        worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=boom_post,
            channel_resolver=lambda: 9999,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        # The handoff outcome carries the short error string but the
        # rendered user-facing response uses a generic template.
        self.assertTrue(outcome.handled)
        text = render_handoff_response(outcome) or ""
        self.assertNotIn("secret-do-not-leak", text)


# ---------------------------------------------------------------------------
# End-to-end: forum save → approval row → reply → obsidian write enqueue
# ---------------------------------------------------------------------------


class EndToEndApprovalToObsidianWriteTests(_HandoffFixture):
    def test_approve_reply_after_forum_handoff_enqueues_obsidian_write(
        self,
    ) -> None:
        from yule_orchestrator.agents.job_queue.approval_reply import (
            handle_approval_reply,
        )
        from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
            JOB_TYPE_OBSIDIAN_WRITE,
            ObsidianWriterWorker,
        )

        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIsNone(outcome.skipped_reason)
        approval_job_id = outcome.approval_job_id
        self.assertIsNotNone(approval_job_id)

        # Now simulate the user replying "이대로 저장" in #승인-대기.
        # The existing M5a-2 handle_approval_reply must convert the
        # SAVED approval row into an obsidian_write row — proving the
        # producer integrates with the existing closed pipeline.
        obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=lambda _r: SimpleNamespace(title="t"),
            write_fn=lambda _n, _v, _r: None,
            vault_root_resolver=lambda _r: Path(self._tmp.name),
        )
        reply_outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=obsidian_worker,
            text="이대로 저장",
            session_id=session.session_id,
            approved_by="masterway",
            source_message_id=message.id,
            source_thread_id=message.channel.id,
        )
        self.assertEqual(reply_outcome.approval_job_id, approval_job_id)
        self.assertIsNotNone(reply_outcome.write_job_id)
        # An obsidian_write row landed in the queue → ready for the
        # ObsidianWriterWorker. Vault write happens AFTER user
        # approval — never before.
        write_rows = [
            j for j in self.queue.list_for_session(session.session_id)
            if j.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(write_rows), 1)
        payload = write_rows[0].payload or {}
        self.assertEqual(payload["approval_id"], approval_job_id)
        self.assertEqual(payload["approved_by"], "masterway")


# ---------------------------------------------------------------------------
# A-M10a — agent-ops audit recording
# ---------------------------------------------------------------------------


class AgentOpsAuditTests(_HandoffFixture):
    """Every handoff decision (queued / dedup / failure) records an
    :mod:`agents.lifecycle.agent_ops_log` entry on session.extra so
    the operator can reconstruct "왜 이 thread 에서 카드가 새로 안
    뜨고 dedup 됐지?" without scraping Discord.
    """

    def _read_audit(self, session) -> list:
        from yule_orchestrator.agents.lifecycle.agent_ops_log import (
            read_agent_ops_audit,
        )

        return list(read_agent_ops_audit(session))

    def test_approval_card_queued_records_l3_audit_entry(self) -> None:
        session = _open_session()
        message = _forum_thread_message()
        _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        rows = self._read_audit(session)
        self.assertGreaterEqual(len(rows), 1)
        # The L3 (knowledge_note_finalize) entry is the human-handoff
        # marker. Producer also stamps the topic_key from the ledger.
        levels = {row.autonomy_level for row in rows}
        self.assertIn("L3_HUMAN_APPROVAL", levels)
        finalize_rows = [
            r for r in rows if r.action == "knowledge_note_finalize"
        ]
        self.assertEqual(len(finalize_rows), 1)
        self.assertEqual(finalize_rows[0].outcome, "approval_card_queued")
        self.assertTrue(finalize_rows[0].topic_key)

    def test_topic_pending_dedup_records_l1_audit_entry(self) -> None:
        from yule_orchestrator.agents.job_queue.forum_obsidian_handoff import (
            SKIPPED_TOPIC_PENDING_APPROVAL,
        )

        session = _open_session()
        message = _forum_thread_message()
        _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        # Second pass — same thread, fresh message id; should hit
        # the topic-level dedup branch.
        message2 = _forum_thread_message(message_id=60002)
        second = _run(
            route_forum_obsidian_save_request(
                message=message2,
                text=message2.content,
                queue=self.queue,
                approval_worker=self.approval_worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertIn(
            second.skipped_reason,
            {SKIPPED_TOPIC_PENDING_APPROVAL, SKIPPED_DUPLICATE_APPROVAL},
        )
        rows = self._read_audit(session)
        # At least one L1 forum_handoff_decision entry alongside the
        # original L3 approval entry.
        l1_rows = [
            r
            for r in rows
            if r.autonomy_level == "L1_AUTO_RECORD_REQUIRED"
            and r.action == "forum_handoff_decision"
        ]
        self.assertGreaterEqual(len(l1_rows), 1)
        # Outcome string carries the dedup reason for grep-ability.
        outcomes = " | ".join(r.outcome for r in l1_rows)
        self.assertTrue(
            "topic_pending" in outcomes
            or "topic_obsidian_in_flight" in outcomes
            or "duplicate_approval" in outcomes
        )

    def test_approval_channel_unset_records_failure_entry(self) -> None:
        from yule_orchestrator.agents.job_queue.approval_worker import (
            ApprovalWorker as Worker,
        )

        async def post_fn(_request, _rendered):
            return {"posted_message_id": 1, "channel_id": 0}

        # channel_resolver returning None forces approval_channel_unset.
        worker = Worker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=post_fn,
            channel_resolver=lambda: None,
        )
        session = _open_session()
        message = _forum_thread_message()
        outcome = _run(
            route_forum_obsidian_save_request(
                message=message,
                text=message.content,
                queue=self.queue,
                approval_worker=worker,
                session_lister=lambda **_: [session],
            )
        )
        self.assertEqual(
            outcome.skipped_reason, SKIPPED_APPROVAL_CHANNEL_UNSET
        )
        rows = self._read_audit(session)
        l1_rows = [
            r
            for r in rows
            if r.autonomy_level == "L1_AUTO_RECORD_REQUIRED"
        ]
        self.assertGreaterEqual(len(l1_rows), 1)
        # Failure outcome carries the diagnostic string.
        self.assertTrue(
            any("approval_channel_unset" in r.outcome for r in l1_rows)
        )


if __name__ == "__main__":
    unittest.main()
