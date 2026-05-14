"""Engineering channel router — routing/intake decisions.

Covers: ``is_engineering_channel`` detection, confirmation signal
parsing, route context env wiring, and the basic
``route_engineering_message`` intake / kickoff flow including the
``decide_routing`` integration. Forum hook + research-pack persistence
tests live in sibling files so a focused failure stays focused.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeMessage,
    FakeIntakeResult,
    FakePlan,
    FakeSession,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    patched_env as _patched_env,
    run as _run,
)

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


class IsEngineeringChannelTests(unittest.TestCase):
    def test_matches_by_channel_id(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        ctx = EngineeringRouteContext(intake_channel_id=111)
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_matches_by_channel_name(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(channel_id=999, name="업무-접수"),
        )
        ctx = EngineeringRouteContext(intake_channel_name="업무-접수")
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_matches_thread_parent(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(
                channel_id=2222,
                name="작업-thread",
                parent_id=111,
                parent_name="업무-접수",
            ),
        )
        ctx = EngineeringRouteContext(intake_channel_id=111)
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_thread_parent_name_match(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(
                channel_id=2222,
                name="작업-thread",
                parent_id=None,
                parent_name="#업무-접수",
            ),
        )
        ctx = EngineeringRouteContext(intake_channel_name="업무-접수")
        self.assertTrue(is_engineering_channel(message=message, route_context=ctx))

    def test_returns_false_when_no_context_configured(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        ctx = EngineeringRouteContext()
        self.assertFalse(is_engineering_channel(message=message, route_context=ctx))

    def test_returns_false_for_planning_channel(self) -> None:
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(channel_id=555, name="planning-chat"),
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
    """Intake-channel happy path + error / continuation branches."""

    def setUp(self) -> None:
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()
        _isolate_cache_for_test(self)

    def _route(
        self,
        *,
        message: FakeMessage,
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
        message = FakeMessage(
            content="안녕",
            channel=FakeChannel(channel_id=999, name="planning-chat"),
        )
        outcome = EngineeringConversationOutcome(content="hi")
        result = self._route(
            message=message,
            conversation_fn=lambda **_: outcome,
        )
        self.assertFalse(result.handled)
        self.send_chunks.assert_not_awaited()

    def test_engineering_message_without_confirmation_only_replies(self) -> None:
        message = FakeMessage(
            content="이번 작업 우선순위 좀 정리해줘",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessage(
            content="좋아요 그대로 진행해 주세요",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약은 이렇습니다.",
            confirmed=True,
            intake_prompt="planning-bot의 자유대화 레이어를 손봐 주세요",
            write_requested=True,
            thread_topic="engineer-feature-abc",
        )
        intake_session = FakeSession(session_id="abc123", task_type="feature")
        intake_plan = FakePlan()
        intake_message = "**[engineering-agent] 새 작업 접수** ..."
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
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
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        existing_session = FakeSession(session_id="open-session", task_type="research")
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
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessage(
            content="좋아 이대로 ㄱㄱ",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        intake_session = FakeSession(session_id="ses1", task_type="ops")
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=intake_session,
                plan=FakePlan(),
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
        message = FakeMessage(
            content="이대로 진행해 주세요",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessage(
            content="이대로 진행해 주세요",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약 갑니다.",
            confirmed=True,
            intake_prompt="작업 진행해 주세요",
        )
        intake_session = FakeSession(session_id="ses-kick-fail", task_type="feature")
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=intake_session,
                plan=FakePlan(),
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
        message = FakeMessage(
            content="브리핑 좀 부탁",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessage(
            content="   ",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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

        message = FakeMessage(
            content="오늘 점심 브리핑 다시 보여줘",
            channel=FakeChannel(channel_id=222, name="일정-관리"),
        )
        result = self._route(
            message=message,
            conversation_fn=lambda **_: EngineeringConversationOutcome(content="should not be sent"),
        )
        self.assertFalse(result.handled)
        self.send_chunks.assert_not_awaited()


class DecideRoutingWiringTests(unittest.TestCase):
    """Production guard: ``route_engineering_message`` must call decide_routing
    and surface its decision on the result."""

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

    def _route(self, *, message: FakeMessage, research_loop_fn, conversation_outcome=None):
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

    def test_route_calls_decide_routing_in_production_path(self) -> None:
        # Production guard: decide_routing must score similarity against
        # the canonical task description (``outcome.intake_prompt``), not
        # the user's confirm reply. ``_confirmed_outcome`` carries
        # intake_prompt="onboarding step 2 정리"; the message body is just
        # a confirm phrase the conversation layer flagged as confirmed.
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        with patch(
            "yule_orchestrator.discord.engineering_channel_router._legacy.decide_routing",
            wraps=__import__(
                "yule_orchestrator.agents.routing", fromlist=["decide_routing"]
            ).decide_routing,
        ) as decide_spy:
            self._route(message=message, research_loop_fn=loop_fn)
        decide_spy.assert_called_once()
        kwargs = decide_spy.call_args.kwargs
        self.assertIn("prompt", kwargs)
        self.assertEqual(kwargs["prompt"], "onboarding step 2 정리")
        self.assertNotEqual(
            kwargs["prompt"],
            "이대로 진행",
            "routing must not run similarity on the confirm phrase",
        )

    def test_route_uses_intake_prompt_for_long_task_after_confirm(self) -> None:
        """Regression for the real Discord confirm flow.

        message.content = "이대로 진행"
        outcome.intake_prompt = 긴 업무 요청

        The router must score open-work matching against the long task,
        not the confirm phrase. Without this fix all confirm turns scored
        zero similarity and routed to create_new_work even when an open
        session covered the same topic.
        """

        long_task = (
            "Obsidian + Discord + Claude를 연결해서 개발팀이 스스로 학습하는 "
            "구조를 설계해줘. 운영 흐름과 메모리 회수 정책 포함."
        )
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="좋습니다.",
            confirmed=True,
            intake_prompt=long_task,
            write_requested=False,
            thread_topic="engineer-feature-abc",
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        with patch(
            "yule_orchestrator.discord.engineering_channel_router._legacy.decide_routing",
            wraps=__import__(
                "yule_orchestrator.agents.routing", fromlist=["decide_routing"]
            ).decide_routing,
        ) as decide_spy:
            self._route(
                message=message,
                research_loop_fn=loop_fn,
                conversation_outcome=outcome,
            )
        decide_spy.assert_called_once()
        kwargs = decide_spy.call_args.kwargs
        self.assertEqual(kwargs["prompt"], long_task)

    def test_route_falls_back_to_message_content_when_no_intake_prompt(
        self,
    ) -> None:
        """Direct-confirm / single-message intake.

        When ``outcome.intake_prompt`` is None/empty (e.g. the user typed
        a complete task description in one message that the conversation
        layer flagged as confirmed in-place), the router falls back to
        ``message.content`` so similarity still has something to score.
        """

        single_message_task = "Stripe pricing hero copy 다시 정리해줘 그대로 진행"
        message = FakeMessage(
            content=single_message_task,
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=None,
            write_requested=False,
            thread_topic=None,
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        with patch(
            "yule_orchestrator.discord.engineering_channel_router._legacy.decide_routing",
            wraps=__import__(
                "yule_orchestrator.agents.routing", fromlist=["decide_routing"]
            ).decide_routing,
        ) as decide_spy:
            self._route(
                message=message,
                research_loop_fn=loop_fn,
                conversation_outcome=outcome,
            )
        decide_spy.assert_called_once()
        kwargs = decide_spy.call_args.kwargs
        self.assertEqual(kwargs["prompt"], single_message_task)

    def test_route_attaches_decision_to_result(self) -> None:
        message = FakeMessage(
            content="라우팅 결과 노출",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        result = self._route(message=message, research_loop_fn=loop_fn)
        self.assertIsNotNone(result.routing_decision)
        # Default flow with no open sessions → CREATE.
        self.assertEqual(result.routing_decision.action, "create_new_work")

    def test_route_joins_existing_when_decision_is_join(self) -> None:
        from yule_orchestrator.agents.routing import EngineeringRoutingDecision

        message = FakeMessage(
            content="기존 작업 이어가자",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = self._confirmed_outcome()
        existing = FakeSession(session_id="open-1", task_type="research")
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
            "yule_orchestrator.discord.engineering_channel_router._legacy.decide_routing",
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

        message = FakeMessage(
            content="비슷한 작업이 두 개 있는데",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = self._confirmed_outcome()

        intake_fn = AsyncMock(side_effect=AssertionError("intake should not run"))
        kickoff_fn = AsyncMock(side_effect=AssertionError("kickoff should not run"))
        loop_fn = AsyncMock(side_effect=AssertionError("loop should not run"))

        with patch(
            "yule_orchestrator.discord.engineering_channel_router._legacy.decide_routing",
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
        joined = "\n".join(sent_payloads)
        self.assertIn("aaa", joined)
        self.assertIn("bbb", joined)


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


class CoerceOutcomeForwardsResearchFieldsTestCase(unittest.TestCase):
    """``_coerce_outcome`` must keep research_pack / collection_outcome /
    role_for_research populated when the conversation layer returns a richer
    response object."""

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


class CommandOnlyAndBotEchoIntakeGuardTests(unittest.TestCase):
    """Live MVP regression: the gateway must never persist a session
    whose prompt is just a confirm phrase ("새 작업으로 진행" /
    "이대로 진행") or a verbatim bot-echo paste-back ("좋습니다. 이대로
    작업을 등록할게요…"). Without these guards the user observed an
    auto-collect loop where pasting bot lines back produced 11
    research sources, the gateway asked for confirmation, the user
    typed a command-only phrase, and the cycle repeated.
    """

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

    def _route(
        self,
        *,
        message,
        outcome,
        intake_fn=None,
        kickoff_fn=None,
    ):
        intake = intake_fn or AsyncMock(
            side_effect=AssertionError(
                "intake_fn must NOT run for command-only / bot-echo prompts"
            )
        )
        kickoff = kickoff_fn or AsyncMock(
            side_effect=AssertionError(
                "kickoff_fn must NOT run when intake is skipped"
            )
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=lambda **_: outcome,
                intake_fn=intake,
                thread_kickoff_fn=kickoff,
                send_chunks=self.send_chunks,
                research_loop_fn=loop_fn,
            )
        )

    def test_bare_imdaero_jinhaeng_with_no_canonical_does_not_intake(self) -> None:
        # User typed "이대로 진행" but the conversation layer didn't
        # have a stashed prior prompt — intake_prompt falls back to
        # the confirm phrase itself. The router must refuse to create
        # a zombie session whose prompt is "이대로 진행".
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt="이대로 진행",  # no canonical recovered
        )
        result = self._route(message=message, outcome=outcome)
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("진행할 업무 원문을 다시 알려주세요", sent)

    def test_bare_saejakeop_jinhaeng_with_no_canonical_does_not_intake(self) -> None:
        # "새 작업으로 진행" alone — explicit create override but no
        # task body. Without a canonical prompt the router asks for
        # the actual work instead of creating a zombie session.
        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt="새 작업으로 진행",
        )
        result = self._route(message=message, outcome=outcome)
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("진행할 업무 원문을 다시 알려주세요", sent)

    def test_bot_echo_paste_back_does_not_intake(self) -> None:
        # User pasted the gateway's own intake confirmation line back
        # into the channel. conversation_fn (a stub here) flagged it
        # as confirmed with the same string as intake_prompt — the
        # router must trip the bot-echo branch of the guard.
        echo = (
            "좋습니다. 이대로 작업을 등록할게요.\n"
            "intake가 만들어지면 세션 ID와 승인 안내를 이어서 드릴게요."
        )
        message = FakeMessage(
            content=echo,
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=echo,
        )
        result = self._route(message=message, outcome=outcome)
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("gateway가 보낸 안내문", sent)

    def test_self_quoted_sufficiency_message_does_not_intake(self) -> None:
        # The "자료가 부족합니다…" template the bot emits in the
        # research-sufficiency path is also covered by the bot-echo
        # guard.
        echo = (
            "자료가 부족합니다. 참고할 링크나 이미지를 올려주실까요?"
        )
        message = FakeMessage(
            content=echo,
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=echo,
        )
        result = self._route(message=message, outcome=outcome)
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("gateway가 보낸 안내문", sent)

    def test_long_research_prompt_followed_by_imdaero_persists_real_prompt(
        self,
    ) -> None:
        # The healthy flow: user typed a long research prompt, the
        # gateway echoed it as last_proposed; user replies "이대로
        # 진행"; conversation_fn returns intake_prompt=<long prompt>.
        # The router must call intake_fn with the LONG prompt.
        long_task = (
            "[Research] 하네스 엔지니어링을 yule-studio-agent에 어떻게 "
            "도입할 수 있을지 조사해줘. 운영 흐름과 메모리 회수 정책 "
            "포함."
        )
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=FakeSession(session_id="ok-1", task_type="research"),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=4242, message="kickoff"
            )
        )
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=long_task,
        )
        result = self._route(
            message=message,
            outcome=outcome,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
        )
        self.assertTrue(result.handled)
        intake_fn.assert_awaited_once()
        kw = intake_fn.await_args.kwargs
        self.assertEqual(kw["prompt"], long_task)

    def test_long_research_prompt_followed_by_saejakeop_persists_real_prompt(
        self,
    ) -> None:
        long_task = (
            "[Research] 결제 모듈 멱등성 검증 흐름을 백엔드에 추가하고 "
            "관련 회귀 시나리오 정리"
        )
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=FakeSession(session_id="ok-2", task_type="feature"),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=4243, message="kickoff"
            )
        )
        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=long_task,
        )
        result = self._route(
            message=message,
            outcome=outcome,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
        )
        self.assertTrue(result.handled)
        intake_fn.assert_awaited_once()
        kw = intake_fn.await_args.kwargs
        self.assertEqual(kw["prompt"], long_task)


if __name__ == "__main__":
    unittest.main()
