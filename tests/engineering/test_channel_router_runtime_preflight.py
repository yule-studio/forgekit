"""Phase 4B — runtime preflight tests for ``route_engineering_message``.

Pin the auto_collect-first regression: when the user's message is a
back-reference / status-style request, the router must not call
``conversation_fn`` (which is the only place ``auto_collect=True`` is
applied). The runtime preflight either joins the matched session or
asks for clarification — and only ``new_work_request`` falls through
to the legacy intake flow.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeMessage,
    FakeIntakeResult,
    FakePlan,
    FakeSession as LegacyFakeSession,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadContinuation,
    EngineeringThreadKickoff,
    route_engineering_message,
)


@dataclass
class RuntimeFakeSession:
    """Recall-friendly session shape (more fields than the legacy
    FakeSession used by the older router tests)."""

    session_id: str
    prompt: str = ""
    task_type: str = "research"
    state: str = "in_progress"
    summary: Optional[str] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    executor_role: Optional[str] = "tech-lead"
    executor_runner: Optional[str] = "claude-code"


def _now(offset_minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


def _channel(channel_id: int = 111, name: str = "업무-접수") -> FakeChannel:
    return FakeChannel(channel_id=channel_id, name=name)


class _PreflightRouteHarness(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()
        self.conversation_fn = AsyncMock(
            side_effect=AssertionError(
                "conversation_fn must NOT run when preflight handles the message"
            )
        )
        self.intake_fn = AsyncMock(
            side_effect=AssertionError("intake must NOT run for non-new-work intents")
        )
        self.kickoff_fn = AsyncMock(
            side_effect=AssertionError("kickoff must NOT run for non-new-work intents")
        )

    def _route(
        self,
        *,
        message,
        list_sessions_fn,
        thread_continuation_fn=None,
    ):
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=self.conversation_fn,
                intake_fn=self.intake_fn,
                thread_kickoff_fn=self.kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=thread_continuation_fn,
                list_sessions_fn=list_sessions_fn,
            )
        )


class SummarizeYesterdayPreflightTests(_PreflightRouteHarness):
    def test_summarize_with_match_joins_existing_thread(self) -> None:
        sessions = [
            RuntimeFakeSession(
                session_id="recent-1",
                prompt="onboarding flow 검토",
                updated_at=_now(-30),
            ),
        ]
        existing = LegacyFakeSession(session_id="recent-1", task_type="research")
        continuation = EngineeringThreadContinuation(
            session=existing,
            thread_id=4242,
            message="기존 thread에 이어 붙였습니다.",
        )
        continuation_fn = AsyncMock(return_value=continuation)

        message = FakeMessage(content="어제 작업 이어서 요약해줘", channel=_channel())

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "recent-1")
        self.assertEqual(result.thread_id, 4242)
        # auto_collect-first guard
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()
        # The clarification prompt was NOT sent — we joined cleanly.
        sent = "\n".join(str(call.args[1]) for call in self.send_chunks.await_args_list)
        self.assertNotIn("어떤 작업을 가리키시는지", sent)
        self.assertIn("기존 thread에 이어 붙였습니다.", sent)


class HermesContinuePreflightTests(_PreflightRouteHarness):
    def test_named_project_continue_routes_to_match(self) -> None:
        sessions = [
            RuntimeFakeSession(
                session_id="hermes-session",
                prompt="헤르메스 RAG 학습 루프 구조",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "헤르메스 학습 루프"}},
            ),
            RuntimeFakeSession(
                session_id="other",
                prompt="결제 모듈 멱등성 검토",
                updated_at=_now(-90),
            ),
        ]
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(session_id="hermes-session", task_type="research"),
                thread_id=8888,
                message="hermes thread 이어 붙였습니다.",
            )
        )
        message = FakeMessage(content="헤르메스 작업 이어서 가자", channel=_channel())

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "hermes-session")
        self.assertEqual(result.thread_id, 8888)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()


class ExecuteStepWithoutMatchTests(_PreflightRouteHarness):
    def test_obsidian_request_without_session_asks_clarification(self) -> None:
        message = FakeMessage(content="Obsidian에 정리해줘", channel=_channel())
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],  # no open sessions
            thread_continuation_fn=AsyncMock(),
        )
        self.assertTrue(result.handled)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()
        sent = "\n".join(str(call.args[1]) for call in self.send_chunks.await_args_list)
        self.assertIn("어떤 작업을 가리키시는지", sent)
        self.assertIn("기존 작업 후속 실행", sent)


class AppendContextWithoutMatchTests(_PreflightRouteHarness):
    def test_append_context_without_session_asks_clarification(self) -> None:
        message = FakeMessage(
            content="이 자료만 기존 작업에 참고로 붙여줘",
            channel=_channel(),
        )
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
            thread_continuation_fn=AsyncMock(),
        )
        self.assertTrue(result.handled)
        self.conversation_fn.assert_not_awaited()
        sent = "\n".join(str(call.args[1]) for call in self.send_chunks.await_args_list)
        self.assertIn("기존 작업에 자료 첨부", sent)


class NewWorkFallthroughTests(unittest.TestCase):
    """Preflight must NOT take over for new-work / confirm / status /
    diagnostic / general chat — those keep flowing through
    conversation_fn so the existing intake + kickoff + research loop
    behave exactly as before."""

    def setUp(self) -> None:  # noqa: D401
        # The legacy fallthrough path runs decide_routing which reads
        # the workflow cache. Other tests in the suite may have written
        # sessions there; isolate so this test's CREATE flow isn't
        # accidentally promoted to JOIN by a stale match.
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()

    def _route_with_outcome(self, message, outcome, *, list_sessions_fn):
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=LegacyFakeSession(session_id="new-1", task_type="feature"),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(thread_id=7777, message="kickoff")
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
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=loop_fn,
                thread_continuation_fn=None,
                list_sessions_fn=list_sessions_fn,
            )
        ), intake_fn, kickoff_fn

    def test_typical_new_work_flows_through_intake(self) -> None:
        message = FakeMessage(
            content="결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘",
            channel=_channel(),
        )
        outcome = EngineeringConversationOutcome(
            content="요약은 이렇습니다.",
            confirmed=True,
            intake_prompt="결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘",
        )
        result, intake_fn, kickoff_fn = self._route_with_outcome(
            message, outcome, list_sessions_fn=lambda **_kw: []
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "new-1")
        intake_fn.assert_awaited_once()
        kickoff_fn.assert_awaited_once()

    def test_explicit_new_work_phrase_flows_through_intake(self) -> None:
        message = FakeMessage(
            content="새 작업으로 진행 결제 모듈 추가",
            channel=_channel(),
        )
        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt="결제 모듈 추가",
        )
        result, intake_fn, _kickoff = self._route_with_outcome(
            message, outcome, list_sessions_fn=lambda **_kw: []
        )
        self.assertTrue(result.handled)
        intake_fn.assert_awaited_once()

    def test_status_question_falls_through_to_conversation(self) -> None:
        message = FakeMessage(content="지금 뭐 하는 중이야?", channel=_channel())
        captured: dict = {}

        def conversation_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringConversationOutcome(
                content="현재 상태입니다.",
                is_status_query=True,
            )

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=AsyncMock(
                    side_effect=AssertionError("status query must not intake")
                ),
                thread_kickoff_fn=AsyncMock(
                    side_effect=AssertionError("status query must not kickoff")
                ),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [],
            )
        )
        self.assertTrue(result.handled)
        # conversation_fn was called — the status diagnostic responder
        # lives there. Important: ``auto_collect=True`` is still in the
        # legacy path but engineering_conversation short-circuits on
        # status_query before it runs.
        self.assertEqual(captured["message_text"], "지금 뭐 하는 중이야?")


class PreflightWithoutInjectionTests(unittest.TestCase):
    """When ``list_sessions_fn`` is not provided the runtime preflight
    is fully disabled and the legacy flow stays intact — every existing
    routing test depends on this contract."""

    def test_no_list_sessions_fn_means_no_short_circuit(self) -> None:
        context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        send_chunks = AsyncMock()
        called = {"conversation": 0}

        def conversation_fn(**_kwargs):
            called["conversation"] += 1
            return EngineeringConversationOutcome(content="hi")

        message = FakeMessage(content="어제 작업 이어서 요약해줘", channel=_channel())

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                # NO list_sessions_fn — preflight is disabled.
            )
        )
        self.assertTrue(result.handled)
        # conversation_fn was called because preflight didn't run.
        self.assertEqual(called["conversation"], 1)


class ThreadBoundContinuationTests(_PreflightRouteHarness):
    """Phase A: when the user is sitting inside a work thread and
    types a short continuation phrase like "기존 세션으로 진행", the
    runtime preflight must join the thread's session without going
    through conversation_fn / auto_collect."""

    def _thread_message(
        self,
        text: str,
        *,
        thread_id: int,
        parent_id: int = 111,
    ):
        # Discord threads expose ``parent_id`` (the channel they live
        # under). The router uses that as the "channel is a thread"
        # signal and feeds thread_id into recall's anchor lookup.
        return FakeMessage(
            content=text,
            channel=FakeChannel(
                channel_id=thread_id,
                name="engineer-feature-abc",
                parent_id=parent_id,
            ),
        )

    def test_force_continue_phrase_joins_thread_session(self) -> None:
        sessions = [
            RuntimeFakeSession(
                session_id="thread-bound",
                prompt="결제 모듈 멱등성 검토",
                thread_id=909090,
                updated_at=_now(-5),
            ),
        ]
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(session_id="thread-bound", task_type="feature"),
                thread_id=909090,
                message="thread에 이어 붙였습니다.",
            )
        )
        message = self._thread_message("기존 세션으로 진행", thread_id=909090)

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "thread-bound")
        self.assertEqual(result.thread_id, 909090)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()


class ClarificationFollowUpSelectionTests(_PreflightRouteHarness):
    """Phase A: gateway shows candidates → user replies "1번" /
    "기존 세션으로 진행" / "이걸로" → preflight resolves the cached
    candidate without calling conversation_fn again."""

    def setUp(self) -> None:  # noqa: D401
        super().setUp()
        # The clarification cache is module-level state. Reset it so
        # tests don't leak candidates across each other when run in
        # the discovery order.
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT.clear()

    def _trigger_clarification(self, message, sessions):
        # First turn: user message classifies as continue/summarize but
        # recall returns ambiguous → preflight stores candidates and
        # sends the clarification template.
        return self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=AsyncMock(),
        )

    def test_numeric_pick_after_clarification(self) -> None:
        # Two ambiguous sessions both touching "hermes 학습" so the
        # initial recall returns candidates without a confident match.
        sessions = [
            RuntimeFakeSession(
                session_id="hermes-a",
                prompt="hermes 학습 루프 구조 설계",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
            RuntimeFakeSession(
                session_id="hermes-b",
                prompt="hermes 학습 루프 구조 검증",
                updated_at=_now(-60),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
        ]
        first = FakeMessage(
            content="hermes 학습 루프 정리해줘",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        # Author id matches in setUp's FakeMessage default; we set it
        # explicitly so the cache key is deterministic.
        first.author = type("A", (), {"id": 4242})()
        first_result = self._trigger_clarification(first, sessions)
        self.assertTrue(first_result.handled)
        # Cache populated.
        from yule_orchestrator.discord import engineering_channel_router as router
        cached = router._GATEWAY_CLARIFICATION_CONTEXT.get((111, 4242))
        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 2)
        # Sanity: gateway sent the clarification template.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("어떤 작업을 가리키시는지", sent)

        # Second turn: user picks "1번".
        # Reset send_chunks to isolate second-turn output.
        self.send_chunks.reset_mock()
        # conversation_fn is still set to AssertionError — must not run.
        second_continuation = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(session_id="hermes-a", task_type="research"),
                thread_id=11,
                message="hermes-a에 이어 붙였습니다.",
            )
        )
        second = FakeMessage(
            content="1번",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        second.author = type("A", (), {"id": 4242})()
        second_result = self._route(
            message=second,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=second_continuation,
        )
        self.assertTrue(second_result.handled)
        self.assertEqual(second_result.session_id, "hermes-a")
        # conversation_fn / intake_fn must NOT run on the selection turn.
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()
        # Cache cleared after a successful selection.
        self.assertNotIn((111, 4242), router._GATEWAY_CLARIFICATION_CONTEXT)

    def test_korean_ordinal_pick(self) -> None:
        sessions = [
            RuntimeFakeSession(
                session_id="hermes-a",
                prompt="hermes 학습 루프 구조 설계",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
            RuntimeFakeSession(
                session_id="hermes-b",
                prompt="hermes 학습 루프 구조 검증",
                updated_at=_now(-60),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
        ]
        first = FakeMessage(
            content="hermes 학습 루프 정리해줘",
            channel=FakeChannel(channel_id=222, name="업무-접수"),
        )
        first.author = type("A", (), {"id": 7000})()
        self._trigger_clarification(first, sessions)
        self.send_chunks.reset_mock()

        second_continuation = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(session_id="hermes-b", task_type="research"),
                thread_id=12,
                message="hermes-b에 이어 붙였습니다.",
            )
        )
        second = FakeMessage(
            content="두 번째 거",
            channel=FakeChannel(channel_id=222, name="업무-접수"),
        )
        second.author = type("A", (), {"id": 7000})()
        result = self._route(
            message=second,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=second_continuation,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "hermes-b")

    def test_demonstrative_phrase_picks_single_cached_candidate(self) -> None:
        """Pre-seed the cache with one candidate and verify "기존 세션
        으로 진행" / "이걸로" land on it without conversation_fn /
        auto_collect running."""

        from yule_orchestrator.discord import engineering_channel_router as router

        scope_key = (333, 8000)
        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = (
            {
                "session_id": "solo",
                "title": "결제 모듈 멱등성",
                "score": 0.5,
                "thread_id": 21,
                "forum_thread_id": None,
                "task_type": "feature",
            },
        )

        continuation = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(session_id="solo", task_type="feature"),
                thread_id=21,
                message="solo에 이어 붙였습니다.",
            )
        )
        # Empty session list — preflight falls back to no recall match,
        # so the only thing that can resolve "기존 세션으로 진행" is the
        # cached candidate selection path.
        message = FakeMessage(
            content="기존 세션으로 진행",
            channel=FakeChannel(channel_id=333, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 8000})()

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
            thread_continuation_fn=continuation,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "solo")
        self.assertEqual(result.thread_id, 21)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()
        # Cache cleared after the successful resolution.
        self.assertNotIn(scope_key, router._GATEWAY_CLARIFICATION_CONTEXT)

    def test_demonstrative_phrase_with_multi_candidates_does_not_pick(self) -> None:
        """``_try_select_candidate`` must NOT silently resolve "이걸로"
        when the cache has multiple candidates — the user has to be
        more specific. Exercise the helper directly so the harness's
        "conversation_fn must not run" guard isn't tripped by the
        legacy fallthrough that follows in the live router."""

        from yule_orchestrator.discord import engineering_channel_router as router

        candidates = (
            {"session_id": "a", "title": "A", "score": 0.4,
             "thread_id": None, "forum_thread_id": None, "task_type": "research"},
            {"session_id": "b", "title": "B", "score": 0.35,
             "thread_id": None, "forum_thread_id": None, "task_type": "research"},
        )
        self.assertIsNone(router._try_select_candidate("이걸로", candidates))
        self.assertIsNone(router._try_select_candidate("기존 세션으로 진행", candidates))
        # Numeric pick, on the other hand, IS unambiguous — sanity
        # check the same helper for the positive path.
        chosen = router._try_select_candidate("1번", candidates)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["session_id"], "a")

    def test_existing_session_phrase_without_cache_or_match_clarifies(self) -> None:
        # No prior clarification, no matching open sessions: "기존
        # 세션으로 진행" must NOT create a fresh session — it asks for
        # clarification because the runtime can't tell which existing
        # work the user means.
        message = FakeMessage(
            content="기존 세션으로 진행",
            channel=FakeChannel(channel_id=444, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 9000})()
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
            thread_continuation_fn=AsyncMock(),
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("어떤 작업을 가리키시는지", sent)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
