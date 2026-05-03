from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringRouteResult,
    EngineeringThreadContinuation,
    EngineeringThreadKickoff,
    detect_confirmation_signal,
    extract_message_attachments,
    is_engineering_channel,
    route_engineering_message,
    should_continue_existing_thread,
    should_start_new_thread,
)


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def _patched_env(values: dict[str, str | None]):
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, prior in previous.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


class _Channel:
    def __init__(
        self,
        *,
        channel_id: int,
        name: str | None,
        parent_id: int | None = None,
        parent_name: str | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        if parent_id is None and parent_name is None:
            self.parent = None
            self.parent_id = None
        else:
            self.parent = _Parent(parent_id, parent_name)
            self.parent_id = parent_id


class _Parent:
    def __init__(self, parent_id: int | None, parent_name: str | None) -> None:
        self.id = parent_id
        self.name = parent_name


class _Author:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Message:
    def __init__(
        self,
        *,
        content: str,
        channel: _Channel,
        author_id: int = 4242,
    ) -> None:
        self.content = content
        self.channel = channel
        self.author = _Author(author_id)
        self.mentions: list[Any] = []


@dataclass
class _FakeSession:
    session_id: str
    task_type: str
    executor_role: str | None = "tech-lead"
    executor_runner: str | None = "claude-code"


@dataclass
class _FakePlan:
    role_sequence: tuple[str, ...] = ("tech-lead", "backend-engineer")


@dataclass
class _FakeIntakeResult:
    session: _FakeSession
    plan: _FakePlan
    message: str


def _extract_prompt(*, message: object, bot_user: object) -> str:
    return str(getattr(message, "content", "") or "")


class IsEngineeringChannelTests(unittest.TestCase):
    def test_matches_by_channel_id(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        ctx = EngineeringRouteContext(intake_channel_id=111)
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_matches_by_channel_name(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(channel_id=999, name="업무-접수"),
        )
        ctx = EngineeringRouteContext(intake_channel_name="업무-접수")
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_matches_thread_parent(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(
                channel_id=2222,
                name="작업-thread",
                parent_id=111,
                parent_name="업무-접수",
            ),
        )
        ctx = EngineeringRouteContext(intake_channel_id=111)
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_thread_parent_name_match(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(
                channel_id=2222,
                name="작업-thread",
                parent_id=None,
                parent_name="#업무-접수",
            ),
        )
        ctx = EngineeringRouteContext(intake_channel_name="업무-접수")
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_returns_false_when_no_context_configured(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        ctx = EngineeringRouteContext()
        self.assertFalse(is_engineering_channel(message=message, route_context=ctx))

    def test_returns_false_for_planning_channel(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(channel_id=555, name="planning-chat"),
        )
        ctx = EngineeringRouteContext(intake_channel_id=111, intake_channel_name="업무-접수")
        self.assertFalse(is_engineering_channel(message=message, route_context=ctx))


class ConfirmationSignalTests(unittest.TestCase):
    def test_detects_korean_confirm_phrases(self) -> None:
        for phrase in (
            "이대로 진행해 줘",
            "확정",
            "ㄱㄱ 시작하자",
            "고고",
            "그대로 가자 진행",
            "오케이 진행해줘",
        ):
            self.assertTrue(
                detect_confirmation_signal(phrase),
                f"expected confirmation for {phrase!r}",
            )

    def test_detects_english_confirm_phrases(self) -> None:
        for phrase in ("let's go", "Go ahead", "kick off please", "Proceed"):
            self.assertTrue(
                detect_confirmation_signal(phrase),
                f"expected confirmation for {phrase!r}",
            )

    def test_does_not_promote_casual_yes(self) -> None:
        for phrase in (
            "그게 뭐야?",
            "yes",
            "네",
            "오케이",
            "",
        ):
            self.assertFalse(
                detect_confirmation_signal(phrase),
                f"did not expect confirmation for {phrase!r}",
            )

    def test_detects_existing_thread_continuation_request(self) -> None:
        self.assertTrue(
            should_continue_existing_thread(
                "이대로 진행",
                "새로 등록하지 말고 열려 있는 스레드에서 이어가줘",
            )
        )
        self.assertFalse(should_continue_existing_thread("새 작업 등록해줘"))
        self.assertTrue(should_start_new_thread("새 작업으로 진행"))


class RouteContextEnvTests(unittest.TestCase):
    def test_reads_env_vars(self) -> None:
        with _patched_env(
            {
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_ID": "1234",
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME": "업무-접수",
            }
        ):
            ctx = EngineeringRouteContext.from_env()
        self.assertEqual(ctx.intake_channel_id, 1234)
        self.assertEqual(ctx.intake_channel_name, "업무-접수")
        self.assertTrue(ctx.configured)

    def test_unconfigured_when_env_missing(self) -> None:
        with _patched_env(
            {
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_ID": None,
                "DISCORD_ENGINEERING_INTAKE_CHANNEL_NAME": None,
            }
        ):
            ctx = EngineeringRouteContext.from_env()
        self.assertFalse(ctx.configured)


class RouteEngineeringMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

    def _route(
        self,
        *,
        message: _Message,
        conversation_fn,
        intake_fn=None,
        thread_kickoff_fn=None,
    ) -> EngineeringRouteResult:
        intake_fn = intake_fn or AsyncMock(side_effect=AssertionError("intake should not run"))
        thread_kickoff_fn = thread_kickoff_fn or AsyncMock(
            side_effect=AssertionError("thread kickoff should not run")
        )
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=intake_fn,
                thread_kickoff_fn=thread_kickoff_fn,
                send_chunks=self.send_chunks,
            )
        )

    def test_non_engineering_channel_returns_unhandled(self) -> None:
        message = _Message(
            content="안녕",
            channel=_Channel(channel_id=999, name="planning-chat"),
        )
        outcome = EngineeringConversationOutcome(content="hi")
        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
        )
        self.assertFalse(result.handled)
        self.send_chunks.assert_not_awaited()

    def test_engineering_message_without_confirmation_only_replies(self) -> None:
        message = _Message(
            content="이번 작업 우선순위 좀 정리해줘",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="우선순위는 다음과 같이 보입니다 …",
        )
        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.conversation_message, outcome.content)
        self.assertIsNone(result.session_id)
        self.send_chunks.assert_awaited_once()
        sent_text = self.send_chunks.await_args.args[1]
        self.assertEqual(sent_text, outcome.content)

    def test_confirmation_runs_intake_and_kickoff(self) -> None:
        message = _Message(
            content="좋아요 그대로 진행해 주세요",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약은 이렇습니다.",
            confirmed=True,
            intake_prompt="planning-bot의 자유대화 레이어를 손봐 주세요",
            write_requested=True,
            thread_topic="engineer-feature-abc",
        )
        intake_session = _FakeSession(session_id="abc123", task_type="feature")
        intake_plan = _FakePlan()
        intake_message = "**[engineering-agent] 새 작업 접수** ..."
        intake_fn = AsyncMock(
            return_value=_FakeIntakeResult(
                session=intake_session,
                plan=intake_plan,
                message=intake_message,
            )
        )
        kickoff = EngineeringThreadKickoff(thread_id=4242, message="kickoff!")
        thread_kickoff_fn = AsyncMock(return_value=kickoff)

        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
            intake_fn=intake_fn,
            thread_kickoff_fn=thread_kickoff_fn,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "abc123")
        self.assertEqual(result.thread_id, 4242)
        self.assertEqual(result.intake_message, intake_message)
        self.assertEqual(result.kickoff_message, "kickoff!")

        intake_fn.assert_awaited_once()
        intake_kwargs = intake_fn.await_args.kwargs
        self.assertEqual(intake_kwargs["prompt"], outcome.intake_prompt)
        self.assertTrue(intake_kwargs["write_requested"])
        self.assertEqual(intake_kwargs["channel_id"], 111)
        self.assertEqual(intake_kwargs["user_id"], 4242)

        thread_kickoff_fn.assert_awaited_once()
        kickoff_kwargs = thread_kickoff_fn.await_args.kwargs
        self.assertIs(kickoff_kwargs["session"], intake_session)
        self.assertIs(kickoff_kwargs["plan"], intake_plan)
        self.assertEqual(kickoff_kwargs["topic"], "engineer-feature-abc")

        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertIn(outcome.content, sent_payloads)
        self.assertIn(intake_message, sent_payloads)

    def test_continuation_request_reuses_existing_thread_without_intake(self) -> None:
        message = _Message(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        existing_session = _FakeSession(session_id="open-session", task_type="research")
        outcome = EngineeringConversationOutcome(
            content="기존 thread를 찾아 이어갈게요.",
            confirmed=True,
            intake_prompt="새로 등록하지 말고 열려 있는 스레드에서 리서치 이어가줘",
        )
        intake_fn = AsyncMock(side_effect=AssertionError("intake should not run"))
        kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=existing_session,
                thread_id=999,
                message="기존 thread에 이어 붙였습니다.",
            )
        )
        captured: dict[str, Any] = {}

        async def research_loop_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringResearchLoopReport(
                forum_status_message="역할별 검토 재개",
                forum_thread_id=777,
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
                research_loop_fn=research_loop_fn,
                thread_continuation_fn=continuation_fn,
            )
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "open-session")
        self.assertEqual(result.thread_id, 999)
        intake_fn.assert_not_awaited()
        kickoff_fn.assert_not_awaited()
        continuation_fn.assert_awaited_once()
        self.assertEqual(captured["session"], existing_session)
        self.assertEqual(captured["thread_id"], 999)
        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertIn("기존 thread에 이어 붙였습니다.", sent_payloads)
        self.assertIn("역할별 검토 재개", sent_payloads)

    def test_continuation_request_without_open_thread_does_not_create_intake(self) -> None:
        message = _Message(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="기존 thread를 찾아 이어갈게요.",
            confirmed=True,
            intake_prompt="새로 등록하지 말고 기존 스레드에서 이어가줘",
        )
        intake_fn = AsyncMock(side_effect=AssertionError("intake should not run"))
        kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))

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
                thread_continuation_fn=AsyncMock(return_value=None),
            )
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.error, "existing engineering thread not found")
        intake_fn.assert_not_awaited()
        kickoff_fn.assert_not_awaited()
        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertTrue(any("새 작업 세션은 만들지 않았습니다" in s for s in sent_payloads))

    def test_keyword_fallback_promotes_to_intake_when_outcome_is_string(self) -> None:
        message = _Message(
            content="좋아 이대로 ㄱㄱ",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        intake_session = _FakeSession(session_id="ses1", task_type="ops")
        intake_fn = AsyncMock(
            return_value=_FakeIntakeResult(
                session=intake_session,
                plan=_FakePlan(),
                message="intake!",
            )
        )
        kickoff = EngineeringThreadKickoff(thread_id=7, message="kickoff!")
        thread_kickoff_fn = AsyncMock(return_value=kickoff)

        result = self._route(
            message=message,
            conversation_fn=lambda **_: "이렇게 진행하면 어떨까요?",
            intake_fn=intake_fn,
            thread_kickoff_fn=thread_kickoff_fn,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "ses1")
        self.assertEqual(result.thread_id, 7)
        intake_fn.assert_awaited_once()
        intake_kwargs = intake_fn.await_args.kwargs
        self.assertEqual(intake_kwargs["prompt"], "좋아 이대로 ㄱㄱ")
        self.assertFalse(intake_kwargs["write_requested"])

    def test_intake_failure_reports_error_without_calling_kickoff(self) -> None:
        message = _Message(
            content="이대로 진행해 주세요",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약 갑니다.",
            confirmed=True,
            intake_prompt="문서 만들어 주세요",
        )
        intake_fn = AsyncMock(side_effect=RuntimeError("dispatcher down"))
        thread_kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))

        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
            intake_fn=intake_fn,
            thread_kickoff_fn=thread_kickoff_fn,
        )

        self.assertTrue(result.handled)
        self.assertIsNone(result.session_id)
        self.assertIn("dispatcher down", result.error or "")
        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertIn(outcome.content, sent_payloads)
        self.assertTrue(any("intake 실패" in payload for payload in sent_payloads))
        thread_kickoff_fn.assert_not_awaited()

    def test_kickoff_failure_keeps_session_and_reports_error(self) -> None:
        message = _Message(
            content="이대로 진행해 주세요",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약 갑니다.",
            confirmed=True,
            intake_prompt="작업 진행해 주세요",
        )
        intake_session = _FakeSession(session_id="ses-kick-fail", task_type="feature")
        intake_fn = AsyncMock(
            return_value=_FakeIntakeResult(
                session=intake_session,
                plan=_FakePlan(),
                message="intake message",
            )
        )
        thread_kickoff_fn = AsyncMock(side_effect=RuntimeError("forbidden"))

        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
            intake_fn=intake_fn,
            thread_kickoff_fn=thread_kickoff_fn,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "ses-kick-fail")
        self.assertIsNone(result.thread_id)
        self.assertEqual(result.error, "forbidden")
        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertTrue(any("thread kickoff 실패" in payload for payload in sent_payloads))

    def test_async_conversation_fn_is_awaited(self) -> None:
        message = _Message(
            content="브리핑 좀 부탁",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(content="요약 응답")

        async def _async_conversation(**_kwargs):
            return outcome

        result = self._route(
            message=message,
            conversation_fn=_async_conversation,
        )
        self.assertTrue(result.handled)
        self.send_chunks.assert_awaited_once()
        self.assertEqual(self.send_chunks.await_args.args[1], outcome.content)

    def test_empty_prompt_does_not_handle(self) -> None:
        message = _Message(
            content="   ",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        result = self._route(
            message=message,
            conversation_fn=lambda **_: EngineeringConversationOutcome(content="ignored"),
        )
        self.assertFalse(result.handled)
        self.send_chunks.assert_not_awaited()

    def test_planning_channel_message_falls_through_unhandled(self) -> None:
        """Engineering router must not steal #일정-관리 / planning conversation messages.

        ``handled=False`` lets the bot's planning conversation layer take over;
        if this regressed, planning-bot users would see "engineer intake" replies.
        """

        message = _Message(
            content="오늘 점심 브리핑 다시 보여줘",
            channel=_Channel(channel_id=222, name="일정-관리"),
        )
        # _route's defaults raise AssertionError if intake_fn / thread_kickoff_fn
        # are ever called, so a clean handled=False here also confirms planning
        # messages never trip the engineering pipeline.
        result = self._route(
            message=message,
            conversation_fn=lambda **_: EngineeringConversationOutcome(content="should not be sent"),
        )
        self.assertFalse(result.handled)
        self.send_chunks.assert_not_awaited()


class _MessageWithAttachments(_Message):
    def __init__(
        self,
        *,
        content: str,
        channel: _Channel,
        attachments: list[Any] | None = None,
        author_id: int = 4242,
    ) -> None:
        super().__init__(content=content, channel=channel, author_id=author_id)
        self.attachments = attachments or []


class RouteEngineeringMessageWithResearchLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

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
            return_value=_FakeIntakeResult(
                session=_FakeSession(session_id="abc", task_type="onboarding-flow"),
                plan=_FakePlan(),
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
        message: _Message,
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
        """Regression: session.extra must contain the research_pack even if
        the research_loop_fn short-circuits as ``insufficient`` (e.g. when
        the confirm message text alone is too short to re-collect from).
        """

        message = _Message(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
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
            "persist_research_artifacts"
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
        self.assertIs(
            call_args.kwargs.get("collection_outcome"), collection_outcome
        )

    def test_research_pack_persisted_when_research_loop_fn_is_none(self) -> None:
        """Even when no research_loop_fn is wired (forum unconfigured / dev
        env), pack must still land in session.extra so Obsidian sync works.
        """

        message = _Message(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
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
            "persist_research_artifacts"
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

    def test_route_calls_decide_routing_in_production_path(self) -> None:
        """Wiring guard: route_engineering_message must invoke decide_routing.

        Earlier we shipped decide_routing as a standalone helper but the
        production router still ran the legacy heuristic. This test
        ensures the wiring stays in place by patching ``decide_routing``
        and asserting the call lands.
        """

        message = _Message(
            content="실제 운영 라우팅 점검",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = self._confirmed_outcome()

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        with patch(
            "yule_orchestrator.discord.engineering_channel_router.decide_routing",
            wraps=__import__(
                "yule_orchestrator.agents.routing", fromlist=["decide_routing"]
            ).decide_routing,
        ) as decide_spy:
            self._route(message=message, research_loop_fn=loop_fn)
        decide_spy.assert_called_once()
        # The production call must pass the original prompt_text so the
        # routing heuristics see what the user typed.
        kwargs = decide_spy.call_args.kwargs
        self.assertIn("prompt", kwargs)
        self.assertEqual(kwargs["prompt"], "실제 운영 라우팅 점검")

    def test_route_attaches_decision_to_result(self) -> None:
        message = _Message(
            content="라우팅 결과 노출",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        result = self._route(message=message, research_loop_fn=loop_fn)
        self.assertIsNotNone(result.routing_decision)
        # Default flow with no open sessions → CREATE.
        self.assertEqual(result.routing_decision.action, "create_new_work")

    def test_route_joins_existing_when_decision_is_join(self) -> None:
        """When decide_routing says JOIN, the router must reuse the matched
        session via thread_continuation_fn instead of creating a new one.
        """

        from yule_orchestrator.agents.routing import (
            EngineeringRoutingDecision,
        )

        message = _Message(
            content="기존 작업 이어가자",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = self._confirmed_outcome()
        existing = _FakeSession(session_id="open-1", task_type="research")
        continuation = EngineeringThreadContinuation(
            session=existing,
            thread_id=4242,
            message="기존 thread에 이어 붙였습니다.",
        )

        async def continuation_fn(**_kwargs):
            return continuation

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        intake_fn = AsyncMock(side_effect=AssertionError("intake should not run"))
        kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))

        with patch(
            "yule_orchestrator.discord.engineering_channel_router.decide_routing",
            return_value=EngineeringRoutingDecision(
                action="join_existing_work",
                matched_session_id="open-1",
                matched_thread_id=4242,
                confidence="high",
                reason="forced join in test",
            ),
        ):
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
                    thread_continuation_fn=continuation_fn,
                )
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "open-1")
        self.assertEqual(result.thread_id, 4242)
        intake_fn.assert_not_awaited()
        kickoff_fn.assert_not_awaited()

    def test_route_asks_for_clarification_when_decision_is_ask(self) -> None:
        from yule_orchestrator.agents.routing import (
            CandidateSummary,
            EngineeringRoutingDecision,
        )

        message = _Message(
            content="비슷한 작업이 두 개 있는데",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        outcome = self._confirmed_outcome()

        intake_fn = AsyncMock(side_effect=AssertionError("intake should not run"))
        kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))
        loop_fn = AsyncMock(side_effect=AssertionError("loop should not run"))

        with patch(
            "yule_orchestrator.discord.engineering_channel_router.decide_routing",
            return_value=EngineeringRoutingDecision(
                action="ask_for_clarification",
                reason="후보 두 건이 비슷합니다",
                candidate_summaries=(
                    CandidateSummary(
                        session_id="aaa",
                        score=0.4,
                        title="Stripe pricing hero",
                        task_type="landing-page",
                        thread_id=10,
                        forum_thread_id=None,
                        why="match",
                    ),
                    CandidateSummary(
                        session_id="bbb",
                        score=0.38,
                        title="Stripe pricing 회귀",
                        task_type="landing-page",
                        thread_id=11,
                        forum_thread_id=None,
                        why="match",
                    ),
                ),
            ),
        ):
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
                )
            )

        self.assertTrue(result.handled)
        intake_fn.assert_not_awaited()
        sent_payloads = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertTrue(
            any("어느 작업에 합류할까요" in s for s in sent_payloads),
            f"sent payloads: {sent_payloads}",
        )
        # Both candidate session ids must surface so the user can pick.
        joined = "\n".join(sent_payloads)
        self.assertIn("aaa", joined)
        self.assertIn("bbb", joined)

    def test_no_persist_call_when_outcome_has_no_research_pack(self) -> None:
        """A bare confirm without recall context must not invoke the persist
        helper (nothing to save) — keeps existing intakes side-effect free.
        """

        message = _Message(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
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
            "persist_research_artifacts"
        ) as persist_spy:
            self._route(
                message=message,
                research_loop_fn=loop_fn,
                conversation_outcome=outcome,
            )

        persist_spy.assert_not_called()

    def test_research_loop_status_message_is_sent(self) -> None:
        message = _MessageWithAttachments(
            content="이대로 진행해 주세요",
            channel=_Channel(channel_id=111, name="업무-접수"),
            attachments=[{"filename": "hero.png"}],
        )

        captured: dict[str, Any] = {}

        async def loop_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringResearchLoopReport(
                forum_status_message="✅ 운영-리서치 forum 게시: thread #777",
                forum_thread_id=777,
                forum_thread_url="https://discord.com/threads/777",
            )

        result = self._route(message=message, research_loop_fn=loop_fn)

        self.assertTrue(result.handled)
        self.assertIsNotNone(result.research_loop_report)
        self.assertEqual(result.research_loop_report.forum_thread_id, 777)
        self.assertEqual(captured.get("session").session_id, "abc")
        self.assertEqual(captured.get("message_text"), "onboarding step 2 정리")
        self.assertEqual(len(captured.get("attachments") or ()), 1)

        sent = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertIn("✅ 운영-리서치 forum 게시: thread #777", sent)

    def test_insufficient_research_followup_is_sent(self) -> None:
        message = _MessageWithAttachments(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )

        async def loop_fn(**_):
            return EngineeringResearchLoopReport(
                follow_up_message="자료가 부족합니다. 참고 링크를 올려주세요.",
                insufficient=True,
            )

        result = self._route(message=message, research_loop_fn=loop_fn)
        self.assertTrue(result.research_loop_report.insufficient)
        sent = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertTrue(
            any(s.startswith("자료가 부족합니다") for s in sent),
            f"follow-up not sent. Got: {sent!r}",
        )

    def test_research_loop_failure_is_non_fatal(self) -> None:
        message = _MessageWithAttachments(
            content="이대로 진행",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )

        async def loop_fn(**_):
            raise RuntimeError("forum API down")

        result = self._route(message=message, research_loop_fn=loop_fn)
        self.assertTrue(result.handled)  # intake + kickoff still landed
        self.assertEqual(result.session_id, "abc")
        self.assertEqual(result.thread_id, 4242)
        self.assertIsNotNone(result.research_loop_report)
        self.assertIn("forum API down", result.research_loop_report.error or "")
        sent = [call.args[1] for call in self.send_chunks.await_args_list]
        self.assertTrue(
            any("research loop 실패" in s for s in sent),
            f"warning not sent. Got: {sent!r}",
        )

    def test_research_loop_skipped_when_no_confirmation(self) -> None:
        message = _Message(
            content="이번 작업 우선순위 좀 정리해줘",
            channel=_Channel(channel_id=111, name="업무-접수"),
        )
        loop_fn = AsyncMock(side_effect=AssertionError("loop should not run"))
        outcome = EngineeringConversationOutcome(content="우선순위 정리 안내")
        result = self._route(
            message=message,
            research_loop_fn=loop_fn,
            conversation_outcome=outcome,
        )
        self.assertTrue(result.handled)
        self.assertIsNone(result.research_loop_report)
        loop_fn.assert_not_awaited()


class ExtractMessageAttachmentsTests(unittest.TestCase):
    def test_returns_empty_tuple_when_attribute_missing(self) -> None:
        message = object()
        self.assertEqual(extract_message_attachments(message), ())

    def test_returns_empty_when_explicit_none(self) -> None:
        class _Msg:
            attachments = None

        self.assertEqual(extract_message_attachments(_Msg()), ())

    def test_passes_through_list_attachments(self) -> None:
        class _Msg:
            attachments = [
                {"filename": "hero.png"},
                {"filename": "spec.pdf"},
            ]

        result = extract_message_attachments(_Msg())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["filename"], "hero.png")

    def test_drops_none_entries(self) -> None:
        class _Msg:
            attachments = [None, {"filename": "a.png"}, None]

        result = extract_message_attachments(_Msg())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "a.png")

    def test_accepts_iterable_attachments(self) -> None:
        def _yield():
            yield {"filename": "one.png"}
            yield {"filename": "two.pdf"}

        class _Msg:
            attachments = _yield()

        result = extract_message_attachments(_Msg())
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# Wire-up tests: research_pack / collection_outcome flow through the router
# ---------------------------------------------------------------------------


class _FakeMessage:
    """Minimal stand-in for ``discord.Message`` used by router tests."""

    def __init__(self, content: str, *, channel_id: int = 999):
        self.content = content
        self.attachments: list = []

        class _Channel:
            id = channel_id
            name = "업무-접수"
            parent = None
            parent_id = None

            async def send(self, *_args, **_kwargs):  # pragma: no cover - tests stub send_chunks
                return None

        class _Author:
            id = 42

        self.channel = _Channel()
        self.author = _Author()


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
    """Conversation layer return shape that mirrors EngineeringConversationResponse."""

    def __init__(self, *, content: str, confirmed: bool, intake_prompt: str,
                 research_pack: Any, collection_outcome: Any,
                 role_for_research: str = "engineering-agent/tech-lead"):
        self.content = content
        self.confirmed = confirmed
        self.intake_prompt = intake_prompt
        self.write_requested = False
        self.thread_topic = None
        self.research_pack = research_pack
        self.collection_outcome = collection_outcome
        self.role_for_research = role_for_research


class RouterPassesResearchContextTestCase(unittest.TestCase):
    """The router must extract research_pack / collection_outcome / role from
    the conversation response and forward them into the research_loop_fn."""

    def test_research_pack_flows_to_research_loop_hook(self) -> None:
        ctx = EngineeringRouteContext(intake_channel_id=999)
        message = _FakeMessage("자료 수집해줘")

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
        # research_loop_fn must receive the research context from the conversation
        self.assertEqual(captured["research_pack"], "<<pack>>")
        self.assertIsNotNone(captured["collection_outcome"])
        self.assertEqual(captured["role_for_research"], "engineering-agent/product-designer")
        # thread_id from kickoff is forwarded so the loop knows where to post
        self.assertEqual(captured["thread_id"], 12345)
        # report propagates into the result
        self.assertIsNotNone(result.research_loop_report)
        self.assertEqual(result.research_loop_report.forum_thread_id, 4242)

    def test_conversation_fn_receives_attachments_and_user_links(self) -> None:
        ctx = EngineeringRouteContext(intake_channel_id=999)
        message = _FakeMessage(
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

        # Router should pre-populate attachments and user links so the
        # conversation layer can hand them straight to auto_collect.
        self.assertEqual(captured["attachments"], ())
        self.assertEqual(
            tuple(captured["user_links"]),
            ("https://example.com/a", "https://example.com/b"),
        )
        self.assertTrue(captured["auto_collect"])


class CoerceOutcomeForwardsResearchFieldsTestCase(unittest.TestCase):
    def test_coerce_pulls_research_fields_from_response(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            _coerce_outcome,
        )

        class _Resp:
            content = "ok"
            confirmed = False
            intake_prompt = None
            write_requested = False
            thread_topic = None
            research_pack = "<<rp>>"
            collection_outcome = "<<co>>"
            role_for_research = "engineering-agent/qa-engineer"

        outcome = _coerce_outcome(_Resp(), prompt_text="x")
        self.assertEqual(outcome.research_pack, "<<rp>>")
        self.assertEqual(outcome.collection_outcome, "<<co>>")
        self.assertEqual(outcome.role_for_research, "engineering-agent/qa-engineer")

    def test_coerce_handles_missing_research_fields(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            _coerce_outcome,
        )

        outcome = _coerce_outcome(
            EngineeringConversationOutcome(content="x"),
            prompt_text="y",
        )
        self.assertIsNone(outcome.research_pack)
        self.assertIsNone(outcome.collection_outcome)
        self.assertIsNone(outcome.role_for_research)


# ---------------------------------------------------------------------------
# Default research loop helper
# ---------------------------------------------------------------------------


class DefaultResearchLoopTestCase(unittest.TestCase):
    def test_publishes_to_forum_when_pack_present(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        publish_calls: list[dict] = []

        async def forum_publisher(**kwargs):
            publish_calls.append(kwargs)

            class _Outcome:
                posted = True
                thread_id = 9999
                thread_url = "https://example.com/threads/9999"

            return _Outcome()

        report = _run(
            make_default_research_loop(
                session=type("S", (), {"session_id": "sess"})(),
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                role_for_research="engineering-agent/product-designer",
                thread_id=42,
                forum_publisher=forum_publisher,
            )
        )

        self.assertEqual(len(publish_calls), 1)
        self.assertEqual(publish_calls[0]["pack"], "<<pack>>")
        self.assertEqual(report.forum_thread_id, 9999)
        self.assertIn("운영-리서치", report.forum_status_message or "")
        self.assertFalse(report.insufficient)

    def test_skips_forum_when_pack_missing(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        async def forum_publisher(**_):
            raise AssertionError("should not be called when pack is None")

        report = _run(
            make_default_research_loop(
                session=None,
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=None,
                research_pack=None,
                forum_publisher=forum_publisher,
            )
        )
        self.assertTrue(report.insufficient)
        self.assertIsNone(report.forum_status_message)

    def test_runs_deliberation_and_posts_to_thread(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        thread_posts: list[str] = []

        async def post_to_thread(thread_id, content):
            thread_posts.append(content)

        @dataclass
        class _Turn:
            rendered: str

        @dataclass
        class _DeliberationResult:
            turns: tuple
            synthesis_text: str

        def deliberation_runner(*, session, research_pack):
            return _DeliberationResult(
                turns=(_Turn(rendered="tech-lead opening"), _Turn(rendered="qa take")),
                synthesis_text="합의안 한 줄",
            )

        report = _run(
            make_default_research_loop(
                session=type("S", (), {"session_id": "sess"})(),
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                thread_id=12345,
                deliberation_runner=deliberation_runner,
                post_to_thread=post_to_thread,
                forum_comment_mode="gateway",
            )
        )

        self.assertEqual(len(thread_posts), 3)  # 2 turns + 1 synthesis
        self.assertIn("tech-lead opening", thread_posts)
        self.assertIn("qa take", thread_posts)
        self.assertIn("합의안 한 줄", thread_posts)
        self.assertIsNone(report.error)

    def test_deliberation_failure_is_non_fatal(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        def deliberation_runner(*, session, research_pack):
            raise RuntimeError("backend down")

        report = _run(
            make_default_research_loop(
                session=type("S", (), {"session_id": "sess"})(),
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                thread_id=12345,
                deliberation_runner=deliberation_runner,
                forum_comment_mode="gateway",
            )
        )
        # Error surfaced but the call did not raise.
        self.assertIn("deliberation 실패", report.error or "")


# ---------------------------------------------------------------------------
# Centralised label helpers (research_collector → conversation/forum reuse)
# ---------------------------------------------------------------------------


class CentralisedLabelTestCase(unittest.TestCase):
    def test_pretty_provider_known_and_unknown(self) -> None:
        from yule_orchestrator.agents.research_collector import pretty_provider

        self.assertEqual(pretty_provider("mock"), "기본 검색(mock)")
        self.assertEqual(pretty_provider("tavily"), "Tavily 검색")
        # Unknown provider falls through unchanged so messages don't crash
        self.assertEqual(pretty_provider("future-provider"), "future-provider")
        self.assertEqual(pretty_provider(None), "알 수 없음")

    def test_pretty_task_type_unknown_passthrough(self) -> None:
        from yule_orchestrator.agents.research_collector import pretty_task_type

        self.assertEqual(pretty_task_type("landing-page"), "랜딩 페이지")
        self.assertEqual(pretty_task_type("design-system"), "design-system")
        self.assertEqual(pretty_task_type(None), "일반")
        self.assertEqual(pretty_task_type(""), "일반")

    def test_pretty_source_type_unknown_passthrough(self) -> None:
        from yule_orchestrator.agents.research_collector import (
            pretty_source_type,
        )
        from yule_orchestrator.agents.research_pack import SourceType

        self.assertEqual(
            pretty_source_type(SourceType.OFFICIAL_DOCS), "공식 문서"
        )
        # Raw enum values still translate
        self.assertEqual(pretty_source_type("github_pr"), "GitHub PR")
        # Unknown string passes through
        self.assertEqual(pretty_source_type("future_kind"), "future_kind")
        # None falls back to "기타"
        self.assertEqual(pretty_source_type(None), "기타")

    def test_pretty_confidence_unknown_passthrough(self) -> None:
        from yule_orchestrator.agents.research_collector import pretty_confidence

        self.assertEqual(pretty_confidence("high"), "신뢰도 높음")
        self.assertEqual(pretty_confidence("medium"), "신뢰도 보통")
        self.assertEqual(pretty_confidence("low"), "신뢰도 낮음")
        # Unknown defaults to medium, never crashes
        self.assertEqual(pretty_confidence("超-high"), "신뢰도 보통")
        self.assertEqual(pretty_confidence(None), "신뢰도 보통")


class ResearchTurnKickoffInForumTestCase(unittest.TestCase):
    """member-bots mode posts one open-call directive into the forum thread."""

    def test_member_bots_mode_posts_open_call_directive_only(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from datetime import datetime as _dt

        session = WorkflowSession(
            session_id="sess-9",
            prompt="hero 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=_dt(2026, 4, 30),
            updated_at=_dt(2026, 4, 30),
            role_sequence=("tech-lead", "ai-engineer", "qa-engineer"),
        )

        async def forum_publisher(**_):
            class _Outcome:
                posted = True
                thread_id = 7777
                thread_url = None

            return _Outcome()

        forum_posts: list[tuple[int, str]] = []

        async def post_to_forum_thread(thread_id, content):
            forum_posts.append((thread_id, content))

        async def post_to_thread(_thread_id, _content):  # pragma: no cover
            raise AssertionError("gateway-mode deliberation must not run")

        def deliberation_runner(*, session, research_pack):  # pragma: no cover
            raise AssertionError("gateway-mode deliberation must not run")

        report = _run(
            make_default_research_loop(
                session=session,
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                role_for_research="engineering-agent/tech-lead",
                thread_id=12345,
                forum_publisher=forum_publisher,
                post_to_forum_thread=post_to_forum_thread,
                post_to_thread=post_to_thread,
                deliberation_runner=deliberation_runner,
                forum_comment_mode="member-bots",
            )
        )

        self.assertEqual(report.forum_thread_id, 7777)
        self.assertEqual(len(forum_posts), 1)
        thread_id, content = forum_posts[0]
        self.assertEqual(thread_id, 7777)
        # Gateway speaks like a facilitator, not a clerk.
        self.assertIn("자료 수집 seed를 올렸어요", content)
        # And drops one role-less open call, not a forced speaking order.
        self.assertIn("[research-open:sess-9]", content)
        self.assertNotIn("[research-turn:", content)
        # ai-engineer/qa-engineer/synthesis directives must NOT show up here.
        self.assertNotIn("ai-engineer]", content)
        self.assertNotIn("qa-engineer]", content)
        self.assertNotIn("tech-lead-synthesis]", content)
        self.assertIsNone(report.error)


class ResearchTurnProtocolTestCase(unittest.TestCase):
    """Protocol-level tests live with the router suite so they run alongside
    the wire-up they enable."""

    def test_parse_marker_extracts_session_and_role(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            parse_research_dispatch_marker,
        )

        self.assertEqual(
            parse_research_dispatch_marker("preamble [research-turn:abc123 ai-engineer] tail"),
            ("abc123", "ai-engineer"),
        )

    def test_parse_marker_returns_none_when_missing(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            parse_research_dispatch_marker,
        )

        self.assertIsNone(parse_research_dispatch_marker("no marker here"))
        # team-turn marker must not match research-turn parser
        self.assertIsNone(
            parse_research_dispatch_marker("[team-turn:abc qa-engineer]")
        )

    def test_parse_open_marker_extracts_session(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            parse_research_open_marker,
        )

        self.assertEqual(
            parse_research_open_marker("job [research-open:sess-1]"),
            "sess-1",
        )

    def test_dispatch_directive_format(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            research_dispatch_directive,
        )

        self.assertEqual(
            research_dispatch_directive("xyz", "qa-engineer"),
            "[research-turn:xyz qa-engineer]",
        )

    def test_role_sequence_normalises_session_role_sequence(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_research_role_sequence,
        )
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from datetime import datetime as _dt

        session = WorkflowSession(
            session_id="x",
            prompt="x",
            task_type="x",
            state=WorkflowState.APPROVED,
            created_at=_dt(2026, 4, 30),
            updated_at=_dt(2026, 4, 30),
            role_sequence=(
                "qa-engineer",  # would-be first role
                "qa-engineer",  # duplicate
                "engineering-agent/ai-engineer",  # full address normalises
            ),
        )
        seq = deliberation_research_role_sequence(session)
        # tech-lead is always inserted first regardless of session input
        self.assertEqual(seq[0], "tech-lead")
        # remaining slots come from session.role_sequence (deduped, short form)
        self.assertIn("qa-engineer", seq)
        self.assertIn("ai-engineer", seq)
        self.assertEqual(len(set(seq)), len(seq))  # no duplicates

    def test_role_sequence_default_when_session_blank(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            DEFAULT_RESEARCH_ROLE_SEQUENCE,
            deliberation_research_role_sequence,
        )
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from datetime import datetime as _dt

        session = WorkflowSession(
            session_id="x",
            prompt="x",
            task_type="x",
            state=WorkflowState.APPROVED,
            created_at=_dt(2026, 4, 30),
            updated_at=_dt(2026, 4, 30),
            role_sequence=(),
        )
        seq = deliberation_research_role_sequence(session)
        self.assertEqual(seq, DEFAULT_RESEARCH_ROLE_SEQUENCE)


class HandleResearchTurnMessageTestCase(unittest.TestCase):
    """Member bots only post when the marker targets their role."""

    def _session(self):
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from datetime import datetime as _dt

        return WorkflowSession(
            session_id="sess-1",
            prompt="hero 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=_dt(2026, 4, 30),
            updated_at=_dt(2026, 4, 30),
            role_sequence=(
                "tech-lead",
                "ai-engineer",
                "product-designer",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
            ),
        )

    def test_marker_for_other_role_returns_none(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="qa-engineer",
            text="[research-turn:sess-1 ai-engineer]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNone(outcome)

    def test_open_call_for_own_role_renders_independent_take(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sess-1]",
            session_loader=lambda _sid: self._session(),
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.role, "ai-engineer")
        self.assertIn("**[ai-engineer]**", outcome.message)
        self.assertIn("독립적으로 제출한 take", outcome.message)
        self.assertNotIn("[research-turn:", outcome.message)
        self.assertIsNone(outcome.next_directive)

    def test_open_call_for_non_participant_returns_none(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="security-engineer",
            text="[research-open:sess-1]",
            session_loader=lambda _sid: self._session(),
        )

        self.assertIsNone(outcome)

    def test_marker_for_own_role_renders_take_and_next_directive(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-turn:sess-1 ai-engineer]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.role, "ai-engineer")
        # ai-engineer take rendered with the role's structured fields
        self.assertIn("**[ai-engineer]**", outcome.message)
        # Next directive points at the next role in the sequence
        self.assertIn(
            "[research-turn:sess-1 product-designer]", outcome.message
        )
        self.assertFalse(outcome.is_synthesis)

    def test_last_role_emits_synthesis_directive(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="qa-engineer",
            text="[research-turn:sess-1 qa-engineer]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNotNone(outcome)
        # The last role hands off to the synthesis sentinel
        self.assertIn(
            "[research-turn:sess-1 tech-lead-synthesis]",
            outcome.message,
        )

    def test_synthesis_marker_renders_synthesis_text(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="tech-lead-synthesis",
            text="[research-turn:sess-1 tech-lead-synthesis]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.is_synthesis)
        # synthesis comment carries the closing summary, no further directive
        self.assertIn("tech-lead 종합", outcome.message)
        self.assertNotIn("[research-turn:", outcome.message)

    def test_tech_lead_bot_handles_synthesis_marker(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="tech-lead",
            text="[research-turn:sess-1 tech-lead-synthesis]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.is_synthesis)
        self.assertIn("tech-lead 종합", outcome.message)

    def test_unknown_session_returns_none(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-turn:nope ai-engineer]",
            session_loader=lambda _sid: None,
        )
        self.assertIsNone(outcome)

    def test_team_turn_marker_does_not_trigger_research_handler(self) -> None:
        """Existing team-turn protocol must keep working untouched."""

        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[team-turn:sess-1 ai-engineer]",
            session_loader=lambda _sid: self._session(),
        )
        self.assertIsNone(outcome)


if __name__ == "__main__":
    unittest.main()
