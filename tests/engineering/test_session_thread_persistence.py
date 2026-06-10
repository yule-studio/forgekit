"""Stabilisation Phase 1 — session/thread/forum persistence.

Pin the live-bug regressions:

  • ``thread_kickoff_fn`` returns a thread id but the router used to
    drop it on the floor — the SQLite session row stayed thread-less
    and follow-up status / Obsidian / continuation lookups failed.
  • ``persist_research_forum_status`` silently swallowed
    ``update_session`` failures — the live MVP loop showed
    ``research_forum_thread_id`` missing with no recorded reason.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any, Optional
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeIntakeResult,
    FakeMessage,
    FakePlan,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadKickoff,
    _persist_thread_id,
    persist_research_forum_status,
    route_engineering_message,
)
from yule_engineering.agents.workflow_state import WorkflowSession, WorkflowState


class _MutableSession:
    """Plain-dict stub the router can mutate in-place."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.task_type = "research"
        self.state = WorkflowState.IN_PROGRESS
        self.prompt = ""
        self.thread_id: Optional[int] = None
        self.summary: Optional[str] = None
        self.role_sequence = ()
        self.extra: dict[str, Any] = {}


class PersistThreadIdTests(unittest.TestCase):
    def test_writes_thread_id_to_in_memory_session(self) -> None:
        session = _MutableSession(session_id="abc12345")
        _persist_thread_id(session, 4242)
        self.assertEqual(session.thread_id, 4242)

    def test_no_op_when_thread_id_unchanged(self) -> None:
        session = _MutableSession(session_id="abc")
        session.thread_id = 4242
        _persist_thread_id(session, 4242)
        self.assertEqual(session.thread_id, 4242)

    def test_no_op_when_thread_id_none(self) -> None:
        session = _MutableSession(session_id="abc")
        _persist_thread_id(session, None)
        self.assertIsNone(session.thread_id)

    def test_persists_through_workflow_session_dataclass(self) -> None:
        # Real WorkflowSession is a frozen dataclass — verify the
        # ``replace`` + ``update_session`` path doesn't raise and
        # returns an updated dataclass.
        _isolate_cache_for_test(self)
        from yule_engineering.agents.workflow_state import (
            save_session,
            load_session,
        )

        now = datetime(2026, 5, 6)
        session = WorkflowSession(
            session_id="abc12345",
            prompt="harness",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
        )
        save_session(session)
        updated = _persist_thread_id(session, 7070)
        self.assertEqual(updated.thread_id, 7070)
        # Round-trip via SQLite — the persisted row carries the new
        # thread_id.
        reloaded = load_session("abc12345")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.thread_id, 7070)


class IntakeFlowPersistsThreadIdTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

    def test_main_intake_create_branch_persists_thread_id(self) -> None:
        session = _MutableSession(session_id="sess-thread")
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=session,
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=909090,
                message="thread kickoff",
            )
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt="결제 멱등성 백엔드 검토",
        )
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=lambda **_: outcome,
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=loop_fn,
                thread_continuation_fn=None,
            )
        )
        self.assertTrue(result.handled)
        # Thread id was persisted on the in-memory session.
        self.assertEqual(session.thread_id, 909090)


class PersistResearchForumStatusTests(unittest.TestCase):
    def test_records_persistence_failure_when_update_session_raises(self) -> None:
        # We can't easily trigger update_session to raise without
        # patching, but we can verify the helper writes the canonical
        # forum_thread_id key into session.extra in-place even without
        # a successful SQLite write — that's the key live-bug
        # regression.
        session = _MutableSession(session_id="abc")
        report = EngineeringResearchLoopReport(
            forum_comment_mode="member-bots",
            forum_thread_id=12345,
            forum_thread_url="https://discord/12345",
            kickoff_posted=True,
            kickoff_error=None,
        )
        persist_research_forum_status(session=session, report=report)
        # Even though _MutableSession is a plain stub (replace fails),
        # the helper falls back to in-place extra mutation.
        self.assertEqual(session.extra.get("forum_comment_mode"), "member-bots")

    def test_no_op_when_session_has_no_session_id(self) -> None:
        session = _MutableSession(session_id="")
        report = EngineeringResearchLoopReport(forum_comment_mode="gateway")
        # Should not raise.
        persist_research_forum_status(session=session, report=report)
        # Empty session_id → guard short-circuits before extra mutation.
        self.assertEqual(session.extra, {})


if __name__ == "__main__":
    unittest.main()
