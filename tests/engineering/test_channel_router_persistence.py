"""Engineering channel router — research-pack persistence regressions.

P1 guard: the router must call ``persist_research_artifacts`` whenever
the conversation layer returns a research_pack (or a collection_outcome),
even when the research loop short-circuits or no loop is wired at all.
Without this, Obsidian sync would silently miss the saved pack.

These tests are kept apart from the routing/forum suites so a failure
on persistence stays focused.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeIntakeResult,
    FakeMessage,
    FakePlan,
    FakeSession,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringRouteResult,
    EngineeringThreadKickoff,
    route_engineering_message,
)


# ---------------------------------------------------------------------------
# Conversation response stand-in matching ``EngineeringConversationResponse``.
# ---------------------------------------------------------------------------


@dataclass
class _StubCollectionOutcome:
    mode_value: str = "auto_collected"
    auto_collected_count: int = 2
    collector_name: str = "mock"
    query: str = "test query"

    @property
    def mode(self):
        class _M:
            value = self.mode_value

        return _M()


class _StubConversationResponse:
    def __init__(
        self,
        *,
        content: str,
        confirmed: bool,
        intake_prompt: str,
        research_pack: Any,
        collection_outcome: Any,
        role_for_research: str = "engineering-agent/tech-lead",
    ) -> None:
        self.content = content
        self.confirmed = confirmed
        self.intake_prompt = intake_prompt
        self.write_requested = False
        self.thread_topic = None
        self.research_pack = research_pack
        self.collection_outcome = collection_outcome
        self.role_for_research = role_for_research


# ---------------------------------------------------------------------------
# Persist-flow tests: pack lands in session.extra regardless of loop state.
# ---------------------------------------------------------------------------


class PersistResearchArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()
        _isolate_cache_for_test(self)

    def _confirmed_outcome(self) -> EngineeringConversationOutcome:
        return EngineeringConversationOutcome(
            content="요약은 이렇습니다.",
            confirmed=True,
            intake_prompt="onboarding step 2 정리",
            write_requested=False,
            thread_topic="engineer-feature-abc",
        )

    def _intake_fn(self):
        return AsyncMock(
            return_value=FakeIntakeResult(
                session=FakeSession(session_id="abc", task_type="onboarding-flow"),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수** ...",
            )
        )

    def _kickoff_fn(self):
        return AsyncMock(
            return_value=EngineeringThreadKickoff(thread_id=4242, message="kickoff!")
        )

    def _route(
        self,
        *,
        message: FakeMessage,
        research_loop_fn,
        conversation_outcome=None,
    ) -> EngineeringRouteResult:
        outcome = conversation_outcome or self._confirmed_outcome()
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=lambda **_: outcome,
                intake_fn=self._intake_fn(),
                thread_kickoff_fn=self._kickoff_fn(),
                send_chunks=self.send_chunks,
                research_loop_fn=research_loop_fn,
            )
        )

    def test_research_pack_is_persisted_after_intake_even_when_loop_insufficient(
        self,
    ) -> None:
        """Even when ``research_loop_fn`` returns insufficient (e.g. confirm
        text alone is too short to re-collect from), the recall pack still
        has to land in session.extra so Obsidian sync sees it."""

        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        pack = object()
        collection_outcome = object()
        outcome = EngineeringConversationOutcome(
            content="좋습니다.",
            confirmed=True,
            intake_prompt="Obsidian 지식 저장 구조 리서치",
            research_pack=pack,
            collection_outcome=collection_outcome,
        )

        async def insufficient_loop_fn(**_kwargs):
            return EngineeringResearchLoopReport(
                follow_up_message="자료가 부족합니다.",
                insufficient=True,
            )

        with patch(
            "yule_orchestrator.discord.engineering_channel_router."
            "research_loop.persist_research_artifacts"
        ) as persist_spy:
            persist_spy.side_effect = lambda session, *_a, **_kw: session
            result = self._route(
                message=message,
                research_loop_fn=insufficient_loop_fn,
                conversation_outcome=outcome,
            )

        self.assertTrue(result.handled)
        persist_spy.assert_called_once()
        call_args = persist_spy.call_args
        self.assertEqual(call_args.args[0].session_id, "abc")
        self.assertIs(call_args.args[1], pack)
        self.assertIs(call_args.kwargs.get("collection_outcome"), collection_outcome)

    def test_research_pack_persisted_when_research_loop_fn_is_none(self) -> None:
        """No research_loop_fn (forum unconfigured / dev env) must still
        land the pack in session.extra so Obsidian sync works."""

        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        pack = object()
        outcome = EngineeringConversationOutcome(
            content="좋습니다.",
            confirmed=True,
            intake_prompt="Obsidian 지식 저장 구조 리서치",
            research_pack=pack,
        )

        with patch(
            "yule_orchestrator.discord.engineering_channel_router."
            "research_loop.persist_research_artifacts"
        ) as persist_spy:
            persist_spy.side_effect = lambda session, *_a, **_kw: session
            result = self._route(
                message=message,
                research_loop_fn=None,
                conversation_outcome=outcome,
            )

        self.assertTrue(result.handled)
        persist_spy.assert_called_once()
        self.assertIs(persist_spy.call_args.args[1], pack)

    def test_no_persist_call_when_outcome_has_no_research_pack(self) -> None:
        """A bare confirm without recall context must not invoke the persist
        helper — keeps regular intakes side-effect free."""

        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="좋습니다.",
            confirmed=True,
            intake_prompt="generic task",
            research_pack=None,
            collection_outcome=None,
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        with patch(
            "yule_orchestrator.discord.engineering_channel_router."
            "research_loop.persist_research_artifacts"
        ) as persist_spy:
            self._route(
                message=message,
                research_loop_fn=loop_fn,
                conversation_outcome=outcome,
            )

        persist_spy.assert_not_called()


# ---------------------------------------------------------------------------
# Conversation → research_loop_fn forwarding (research_pack flow-through).
# ---------------------------------------------------------------------------


class _RouterFakeMessage:
    """Stand-in for ``discord.Message`` used by router-forwarding tests."""

    def __init__(self, content: str, *, channel_id: int = 999):
        self.content = content
        self.attachments: list = []

        class _Channel:
            id = channel_id
            name = "업무-접수"
            parent = None
            parent_id = None

            async def send(self, *_args, **_kwargs):  # pragma: no cover
                return None

        class _Author:
            id = 42

        self.channel = _Channel()
        self.author = _Author()


class RouterPassesResearchContextTests(unittest.TestCase):
    """The router must forward research_pack / collection_outcome /
    role_for_research from the conversation response into research_loop_fn."""

    def setUp(self) -> None:
        _isolate_cache_for_test(self)

    def test_research_pack_flows_to_research_loop_hook(self) -> None:
        ctx = EngineeringRouteContext(intake_channel_id=999)
        message = _RouterFakeMessage("자료 수집해줘")

        async def conversation_fn(*, message_text, **kwargs):
            return _StubConversationResponse(
                content="좋아요. 먼저 1차 자료를 모아볼게요.",
                confirmed=True,
                intake_prompt=message_text,
                research_pack="<<pack>>",
                collection_outcome=_StubCollectionOutcome(),
                role_for_research="engineering-agent/product-designer",
            )

        @dataclass
        class _IntakeReturn:
            session: Any
            plan: Any
            message: str

        def intake_fn(*, prompt, write_requested, channel_id, user_id):
            return _IntakeReturn(
                session=type("S", (), {"session_id": "sess-1"})(),
                plan=None,
                message="intake summary",
            )

        async def thread_kickoff_fn(*, channel, session, plan, topic):
            return EngineeringThreadKickoff(thread_id=12345, message="kickoff")

        send_chunks = AsyncMock()
        captured: dict = {}

        async def research_loop_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringResearchLoopReport(
                forum_status_message="운영-리서치에 자료 정리를 남겼어요.",
                forum_thread_id=4242,
            )

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=None,
                route_context=ctx,
                extract_prompt=lambda **_: message.content,
                conversation_fn=conversation_fn,
                intake_fn=intake_fn,
                thread_kickoff_fn=thread_kickoff_fn,
                send_chunks=send_chunks,
                research_loop_fn=research_loop_fn,
            )
        )

        self.assertTrue(result.handled)
        self.assertEqual(captured["research_pack"], "<<pack>>")
        self.assertIsNotNone(captured["collection_outcome"])
        self.assertEqual(
            captured["role_for_research"], "engineering-agent/product-designer"
        )
        self.assertEqual(captured["thread_id"], 12345)
        self.assertIsNotNone(result.research_loop_report)
        self.assertEqual(result.research_loop_report.forum_thread_id, 4242)

    def test_conversation_fn_receives_attachments_and_user_links(self) -> None:
        ctx = EngineeringRouteContext(intake_channel_id=999)
        message = _RouterFakeMessage(
            "관련 자료 https://example.com/a https://example.com/b 참고",
        )

        captured: dict = {}

        def conversation_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringConversationOutcome(content="ack")

        _run(
            route_engineering_message(
                message=message,
                bot_user=None,
                route_context=ctx,
                extract_prompt=lambda **_: message.content,
                conversation_fn=conversation_fn,
                intake_fn=lambda **_: None,
                thread_kickoff_fn=AsyncMock(),
                send_chunks=AsyncMock(),
            )
        )

        self.assertEqual(captured["attachments"], ())
        self.assertEqual(
            tuple(captured["user_links"]),
            ("https://example.com/a", "https://example.com/b"),
        )
        self.assertTrue(captured["auto_collect"])


if __name__ == "__main__":
    unittest.main()
