"""Discord approval reply router — A-M6.1b-2 unit tests.

Pin every branch of :func:`route_approval_channel_message`:

  * channel matcher (ID first / NAME fallback)
  * non-approval channel → handled=False (fall-through)
  * bot self-message → handled=False (no recursion)
  * empty body in approval channel → handled=True silent skip
  * APPROVE → handle_approval_reply enqueues + friendly response
  * duplicate APPROVE → "이미 저장 큐에 들어가 있다" response
  * REJECT → rejection recorded + friendly response
  * HOLD / UNCLEAR → "의도를 확인할 수 없다" response, no SQLite scan
  * no matching session → "카드를 못 찾았다" response
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

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
from yule_engineering.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    ObsidianWriterWorker,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.discord.approval.reply_router import (
    RESPONSE_APPROVED,
    RESPONSE_DUPLICATE,
    RESPONSE_HOLD_OR_UNCLEAR,
    RESPONSE_NO_MATCH,
    RESPONSE_REJECTED,
    is_approval_channel_message,
    route_approval_channel_message,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Channel matcher
# ---------------------------------------------------------------------------


def _msg(*, channel_id: int, channel_name: str = "", content: str = "", author_bot: bool = False, author_id: int = 1, message_id: int = 999):
    channel = SimpleNamespace(id=channel_id, name=channel_name)
    author = SimpleNamespace(
        id=author_id,
        bot=author_bot,
        name="masterway",
        global_name="masterway",
    )
    return SimpleNamespace(
        channel=channel, author=author, content=content, id=message_id
    )


class ChannelMatcherTests(unittest.TestCase):
    def test_id_first_match(self) -> None:
        msg = _msg(channel_id=12345, channel_name="other-name")
        self.assertTrue(
            is_approval_channel_message(
                message=msg,
                approval_channel_id=12345,
                approval_channel_name="승인-대기",
            )
        )

    def test_name_fallback_when_id_unset(self) -> None:
        msg = _msg(channel_id=99, channel_name="승인-대기")
        self.assertTrue(
            is_approval_channel_message(
                message=msg,
                approval_channel_id=None,
                approval_channel_name="승인-대기",
            )
        )

    def test_neither_match_returns_false(self) -> None:
        msg = _msg(channel_id=99, channel_name="other")
        self.assertFalse(
            is_approval_channel_message(
                message=msg,
                approval_channel_id=12345,
                approval_channel_name="승인-대기",
            )
        )

    def test_name_match_is_substring_tolerant(self) -> None:
        # Operator renamed the channel; the substring still matches.
        msg = _msg(channel_id=99, channel_name="eng-승인-대기")
        self.assertTrue(
            is_approval_channel_message(
                message=msg,
                approval_channel_id=None,
                approval_channel_name="승인-대기",
            )
        )


# ---------------------------------------------------------------------------
# Router — all branches
# ---------------------------------------------------------------------------


class _RouterFixture(unittest.TestCase):
    """Fresh queue + workers + a session row each test can resolve to."""

    SESSION_ID = "sess-router-1"
    APPROVAL_CHANNEL_ID = 70000

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        # ApprovalWorker stub: always succeeds, records noth.
        async def _post_fn(_request, _rendered):
            return {"posted_message_id": 1}

        self.approval_worker = ApprovalWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            post_fn=_post_fn,
            channel_resolver=lambda: 8888,
        )
        # ObsidianWriterWorker stub.
        self.writes: List[tuple] = []

        def _render_fn(_request):
            return {"rendered": True}

        def _write_fn(_note, _vault, _request):
            self.writes.append(_request)
            return None

        self.obsidian_worker = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=_render_fn,
            write_fn=_write_fn,
            vault_root_resolver=lambda _r: Path(self._tmp.name) / "vault",
        )

        # Captured outbound channel sends (text replies).
        self.sent: List[str] = []

        async def _send_chunks(_channel, text: str, *args, **kwargs):
            self.sent.append(text)

        self.send_chunks = _send_chunks

        # Session lister fake — returns one open session whose
        # forum_thread matches the approval channel id, so the
        # router resolves the reply to it without us threading
        # the session_id through the test message.
        self.fake_session = SimpleNamespace(
            session_id=self.SESSION_ID,
            thread_id=None,
            updated_at="2026-05-07T13:00",
            extra={
                "research_forum_thread_id": self.APPROVAL_CHANNEL_ID,
            },
        )
        self.session_lister = lambda: [self.fake_session]

    def _seed_obsidian_approval_card(self) -> str:
        """Drive ApprovalWorker.run_one so a SAVED approval_post row
        lands in the queue (the state replies target)."""

        request = ApprovalRequest(
            session_id=self.SESSION_ID,
            approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
            title="결정 노트",
            summary="x",
            requested_action="vault 저장",
            created_by="tech-lead",
            source_thread_id=self.APPROVAL_CHANNEL_ID,
            source_message_id=42,
            extra={"decision_id": "dec-router-1"},
        )
        outcome = _run(self.approval_worker.run_one(request))
        assert outcome.job is not None
        return outcome.job.job_id


class RouterShortCircuitTests(_RouterFixture):
    def test_message_outside_approval_channel_falls_through(self) -> None:
        msg = _msg(channel_id=99999, content="승인")
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                send_chunks=self.send_chunks,
            )
        )
        # handled=False so on_message keeps its existing engineering
        # route — the legacy in-channel approval UX still works on
        # work threads.
        self.assertFalse(result.handled)
        self.assertEqual(self.sent, [])

    def test_bot_self_message_is_ignored(self) -> None:
        # If the bot's own friendly response triggered another
        # routing pass, we'd recursively post — never let bot
        # messages reach the matcher.
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="승인",
            author_bot=True,
        )
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                send_chunks=self.send_chunks,
            )
        )
        self.assertFalse(result.handled)
        self.assertEqual(self.sent, [])

    def test_empty_message_in_approval_channel_is_silent_handled(self) -> None:
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="")
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                send_chunks=self.send_chunks,
            )
        )
        # handled=True so on_message doesn't fall through to the
        # engineering route, but no reply is sent.
        self.assertTrue(result.handled)
        self.assertEqual(self.sent, [])
        self.assertEqual(result.skipped_reason, "empty_message")


class RouterApproveBranchTests(_RouterFixture):
    def test_approve_enqueues_obsidian_write_and_replies(self) -> None:
        self._seed_obsidian_approval_card()
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="이대로 저장")
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        self.assertTrue(result.handled)
        self.assertIsNotNone(result.outcome)
        # An obsidian_write row landed in the queue.
        rows = [
            r for r in self.queue.list_for_session(self.SESSION_ID)
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(rows), 1)
        # Friendly user response sent.
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Obsidian 저장 큐에 넣었습니다", self.sent[0])
        # Reply contains the write job id so an operator can
        # cross-reference the queue row from the channel.
        self.assertIn(rows[0].job_id, self.sent[0])

    def test_duplicate_approve_replies_with_already_queued(self) -> None:
        self._seed_obsidian_approval_card()
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="승인")

        first = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        second = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        # First: APPROVED. Second: DUPLICATE.
        self.assertEqual(first.response_sent, self.sent[0])
        self.assertEqual(second.response_sent, RESPONSE_DUPLICATE)
        self.assertEqual(self.sent[1], RESPONSE_DUPLICATE)


class RouterRejectBranchTests(_RouterFixture):
    def test_reject_records_audit_and_replies(self) -> None:
        self._seed_obsidian_approval_card()
        captured = []

        def _persist(**kwargs):
            captured.append(kwargs)

        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID, content="저장하지 마"
        )
        # Drive through handle_approval_reply directly (the router's
        # adapter calls it under the hood) so we can pass the test
        # persist_fn. Easier than patching for now — the persist
        # injection + outcome shape is what really matters.
        from yule_engineering.agents.job_queue.approval_reply import (
            handle_approval_reply,
        )

        outcome = handle_approval_reply(
            queue=self.queue,
            obsidian_worker=self.obsidian_worker,
            text="저장하지 마",
            session_id=self.SESSION_ID,
            approved_by="masterway",
            persist_rejection_fn=_persist,
        )
        self.assertTrue(outcome.rejection_recorded)
        # No obsidian_write row.
        rows = [
            r for r in self.queue.list_for_session(self.SESSION_ID)
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(rows, [])
        # The router itself, when wired with the default persist,
        # produces RESPONSE_REJECTED.
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.response_sent, RESPONSE_REJECTED)


class RouterHoldUnclearTests(_RouterFixture):
    def test_unclear_message_replies_with_clarification(self) -> None:
        self._seed_obsidian_approval_card()
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID,
            content="음 좀 더 보고 결정할게요",
        )
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.response_sent, RESPONSE_HOLD_OR_UNCLEAR)
        # No obsidian_write row.
        rows = [
            r for r in self.queue.list_for_session(self.SESSION_ID)
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(rows, [])

    def test_hold_message_replies_with_clarification(self) -> None:
        self._seed_obsidian_approval_card()
        msg = _msg(
            channel_id=self.APPROVAL_CHANNEL_ID, content="잠시 보류"
        )
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=self.session_lister,
                send_chunks=self.send_chunks,
            )
        )
        self.assertEqual(result.response_sent, RESPONSE_HOLD_OR_UNCLEAR)


class RouterNoMatchTests(_RouterFixture):
    def test_approve_with_no_session_replies_no_match(self) -> None:
        # No session lister fake → no_session_for_reply → NO_MATCH.
        msg = _msg(channel_id=self.APPROVAL_CHANNEL_ID, content="승인")
        result = _run(
            route_approval_channel_message(
                message=msg,
                bot_user=SimpleNamespace(id=0),
                queue=self.queue,
                obsidian_worker=self.obsidian_worker,
                approval_channel_id=self.APPROVAL_CHANNEL_ID,
                session_lister=lambda: (),
                send_chunks=self.send_chunks,
            )
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.response_sent, RESPONSE_NO_MATCH)


if __name__ == "__main__":
    unittest.main()
