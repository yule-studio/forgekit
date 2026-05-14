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
        research_loop_fn=None,
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
                research_loop_fn=research_loop_fn,
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
        # New schema: dict with "candidates" tuple + "canonical_prompt".
        candidates = cached.get("candidates") if isinstance(cached, dict) else cached
        self.assertEqual(len(candidates), 2)
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


class ContinuationResearchRestartTests(_PreflightRouteHarness):
    """Live MVP regression: when the user resumes a session whose
    research_pack was never built (the initial intake confirmation was
    a command-only phrase like "새 작업으로 진행" so research never ran),
    a continuation message that asks for research must re-trigger the
    research_loop_fn against the matched session — otherwise the user
    keeps typing into a thread where nothing is collecting forum data.

    Tests use thread-bound messages so the recall step finds the
    matched session by thread anchor (the live bug always sat inside an
    open work thread). That way the assertion is purely about whether
    the preflight passes ``research_loop_fn`` through, not about
    keyword scoring."""

    def _thread_message(
        self,
        text: str,
        *,
        thread_id: int,
        parent_id: int = 111,
    ):
        return FakeMessage(
            content=text,
            channel=FakeChannel(
                channel_id=thread_id,
                name="engineer-feature-abc",
                parent_id=parent_id,
            ),
        )

    def test_research_restart_when_pack_missing_and_keyword_present(self) -> None:
        # Live bug shape: session was created from a "새 작업으로 진행"
        # confirmation, never grew a research_pack, and the user's
        # continuation message asks for fresh research.
        sessions = [
            RuntimeFakeSession(
                session_id="abc123def456",
                prompt="새 작업으로 진행",
                thread_id=3003,
                updated_at=_now(-5),
                extra={},
            ),
        ]
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(
                    session_id="abc123def456", task_type="research"
                ),
                thread_id=3003,
                message="thread에 이어 붙였습니다.",
            )
        )

        loop_calls: list[dict] = []

        async def loop_fn(**kwargs):
            loop_calls.append(kwargs)
            return EngineeringResearchLoopReport(
                follow_up_message="research loop ran"
            )

        message = self._thread_message(
            "여기 thread에서 이어 — [Research] 하네스 엔지니어링 자동화 검토, "
            "운영-리서치에 자료 좀 더 모아줘",
            thread_id=3003,
        )
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
            research_loop_fn=loop_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "abc123def456")
        # research_loop_fn fired exactly once because the matched
        # session had no research_pack and the prompt carried a
        # research keyword.
        self.assertEqual(len(loop_calls), 1)

    def test_research_restart_skipped_when_pack_already_present(self) -> None:
        # research_pack already exists — re-running collection would
        # double the work. The preflight must NOT pass research_loop_fn
        # through to the join helper.
        sessions = [
            RuntimeFakeSession(
                session_id="hermes-with-pack",
                prompt="헤르메스 학습 루프 구조 정리",
                thread_id=8001,
                updated_at=_now(-5),
                extra={"research_pack": {"title": "헤르메스 학습 루프"}},
            ),
        ]
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(
                    session_id="hermes-with-pack", task_type="research"
                ),
                thread_id=8001,
                message="thread에 이어 붙였습니다.",
            )
        )
        loop_called = {"count": 0}

        async def loop_fn(**_kwargs):
            loop_called["count"] += 1
            return EngineeringResearchLoopReport()

        message = self._thread_message(
            "여기 thread에서 이어 — [Research] 추가 자료 좀 더 모아줘",
            thread_id=8001,
        )
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
            research_loop_fn=loop_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "hermes-with-pack")
        # research_loop_fn must NOT have been called — pack is already
        # there, the user's "자료 좀 모아줘" applies to a thread that
        # already has a published collection.
        self.assertEqual(loop_called["count"], 0)

    def test_research_restart_skipped_without_research_keyword(self) -> None:
        # Pack is missing but the prompt has no research-shaped wording
        # — preserve the legacy "no auto research loop on join"
        # behaviour so plain resume / status pings don't kick off forum
        # sweeps.
        sessions = [
            RuntimeFakeSession(
                session_id="bare-resume",
                prompt="결제 모듈 멱등성 검증 흐름",
                thread_id=8002,
                updated_at=_now(-5),
                extra={},
            ),
        ]
        continuation_fn = AsyncMock(
            return_value=EngineeringThreadContinuation(
                session=LegacyFakeSession(
                    session_id="bare-resume", task_type="feature"
                ),
                thread_id=8002,
                message="thread 이어 붙였습니다.",
            )
        )
        loop_called = {"count": 0}

        async def loop_fn(**_kwargs):
            loop_called["count"] += 1
            return EngineeringResearchLoopReport()

        message = self._thread_message(
            "기존 세션으로 진행",
            thread_id=8002,
        )
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: sessions,
            thread_continuation_fn=continuation_fn,
            research_loop_fn=loop_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(loop_called["count"], 0)


class ClarificationCanonicalPromptHandoffTests(unittest.TestCase):
    """Live MVP regression: when the gateway shows a candidate
    clarification on turn 1 (`이어갈 세션 ID를…`) and the user replies
    with a routing-command on turn 2 (`새 작업으로 진행` or
    `1번` / `기존 세션 …`), the canonical Research원문 from turn 1
    must be re-used for session.prompt + research_loop prompt_text +
    forum body. Pure routing-command text must never become the
    canonical prompt.
    """

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT.clear()

    def _seed_canonical(
        self,
        *,
        canonical_prompt: str,
        scope_key: tuple,
        candidates: tuple = (),
    ) -> None:
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = {
            "candidates": candidates,
            "canonical_prompt": canonical_prompt,
        }

    def _route(
        self,
        *,
        message,
        intake_fn,
        kickoff_fn,
        research_loop_fn=None,
        list_sessions_fn=None,
        thread_continuation_fn=None,
    ):
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError(
                        "conversation_fn must NOT run for clarification "
                        "follow-up create-override"
                    )
                ),
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=research_loop_fn,
                thread_continuation_fn=thread_continuation_fn,
                list_sessions_fn=list_sessions_fn or (lambda **_kw: []),
            )
        )

    def test_new_work_selection_uses_canonical_for_intake(self) -> None:
        canonical = (
            "[Research] 하네스 엔지니어링을 yule-studio-agent에 어떻게 도입할 수 "
            "있을지 조사해줘. 운영 흐름과 메모리 회수 정책 포함."
        )
        scope_key = (111, 4242)
        self._seed_canonical(
            canonical_prompt=canonical,
            scope_key=scope_key,
            candidates=(
                {
                    "session_id": "old-1",
                    "title": "이전 후보 1",
                    "score": 0.4,
                    "thread_id": None,
                    "forum_thread_id": None,
                    "task_type": "research",
                },
            ),
        )

        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=LegacyFakeSession(
                    session_id="new-1", task_type="research"
                ),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=9999, message="kickoff"
            )
        )

        message = FakeMessage(
            content="기존 후보들은 다 제거 해주고 새 작업으로 진행해줘",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 4242})()

        result = self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "new-1")
        intake_fn.assert_awaited_once()
        kw = intake_fn.await_args.kwargs
        # The smoking gun: intake_fn must receive the canonical
        # Research원문, NOT the verbose routing-command paraphrase.
        self.assertEqual(kw["prompt"], canonical)

    def test_new_work_selection_passes_canonical_to_research_loop(self) -> None:
        canonical = (
            "[Research] 결제 모듈 멱등성 검증 흐름 백엔드 추가 + 회귀 시나리오"
        )
        scope_key = (111, 5252)
        self._seed_canonical(canonical_prompt=canonical, scope_key=scope_key)

        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=LegacyFakeSession(
                    session_id="new-2", task_type="feature"
                ),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=12345, message="kickoff"
            )
        )

        loop_calls: list[dict] = []

        async def loop_fn(**kwargs):
            loop_calls.append(kwargs)
            return EngineeringResearchLoopReport()

        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 5252})()

        result = self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
            research_loop_fn=loop_fn,
        )
        self.assertTrue(result.handled)
        self.assertEqual(len(loop_calls), 1)
        # research_loop_fn must see the canonical Research원문 — never
        # the routing-command "새 작업으로 진행". Forum publishers and
        # role-bot prefaces all read off this prompt_text.
        self.assertEqual(loop_calls[0]["message_text"], canonical)

    def test_clarification_cache_cleared_after_successful_create(self) -> None:
        canonical = "[Research] something substantive"
        scope_key = (111, 6363)
        self._seed_canonical(canonical_prompt=canonical, scope_key=scope_key)

        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=LegacyFakeSession(
                    session_id="new-3", task_type="research"
                ),
                plan=FakePlan(),
                message="ok",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(thread_id=22, message="ok")
        )

        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 6363})()

        self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
        )
        from yule_orchestrator.discord import engineering_channel_router as router

        self.assertNotIn(scope_key, router._GATEWAY_CLARIFICATION_CONTEXT)

    def test_no_canonical_means_no_session_created(self) -> None:
        # Cache exists with candidates but missing canonical_prompt —
        # older entry from before the Phase B fix. The router must
        # NOT spawn a session whose prompt is the routing-command
        # phrase ("새 작업으로 진행").
        from yule_orchestrator.discord import engineering_channel_router as router

        scope_key = (111, 7474)
        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = {
            "candidates": (
                {
                    "session_id": "old",
                    "title": "stale",
                    "score": 0.3,
                    "thread_id": None,
                    "forum_thread_id": None,
                    "task_type": "research",
                },
            ),
            # canonical_prompt intentionally missing
        }

        intake_fn = AsyncMock(
            side_effect=AssertionError(
                "intake_fn must NOT run without a canonical prompt"
            )
        )
        kickoff_fn = AsyncMock(
            side_effect=AssertionError(
                "kickoff_fn must NOT run without a canonical prompt"
            )
        )

        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 7474})()

        result = self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
        )
        self.assertTrue(result.handled)
        # Routing-prompt guard fired — clarification message sent.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("진행할 업무 원문", sent)

    def test_existing_session_pick_uses_canonical_for_append(self) -> None:
        # Live regression: user picks "1번" — the join helper must
        # append the stored canonical_prompt to the matched session,
        # not the bare "1번" reply.
        canonical = "[Research] 운영 메트릭 수집 자동화 검토 + KPI 정의"
        scope_key = (111, 8585)
        candidate = {
            "session_id": "match-1",
            "title": "운영 메트릭",
            "score": 0.42,
            "thread_id": 4444,
            "forum_thread_id": None,
            "task_type": "research",
        }
        self._seed_canonical(
            canonical_prompt=canonical,
            scope_key=scope_key,
            candidates=(candidate,),
        )

        captured: dict = {}

        async def continuation_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringThreadContinuation(
                session=LegacyFakeSession(
                    session_id="match-1", task_type="research"
                ),
                thread_id=4444,
                message="기존 thread에 이어 붙였습니다.",
            )

        intake_fn = AsyncMock(
            side_effect=AssertionError("intake must NOT run on candidate pick")
        )
        kickoff_fn = AsyncMock(
            side_effect=AssertionError("kickoff must NOT run on candidate pick")
        )

        message = FakeMessage(
            content="1번",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 8585})()

        self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
            thread_continuation_fn=continuation_fn,
            list_sessions_fn=lambda **_kw: [],
        )
        # The continuation helper must have received the canonical
        # Research원문 as the appended prompt — not the "1번" reply.
        self.assertEqual(captured.get("prompt"), canonical)


class P0MClarificationCreatePrecedenceTests(unittest.TestCase):
    """P0-M regression — clarification-create-new-work MUST win over the
    runtime preflight and the conversation-layer APPROVAL_ACTION
    downgrade.

    User-reported failure flow (pre-fix):

      1. user types a new task body
      2. gateway sends candidate-vs-new-work clarification (cache
         populated with canonical_prompt)
      3. user replies "새 작업으로 진행"
      4. expected: drive_clarification_create_new_work runs with the
         cached canonical → new session
      5. actual: the message lands in conversation_fn → CONFIRM_INTAKE
         is downgraded to APPROVAL_ACTION ("승인 반영했습니다") because
         "새 작업으로 진행" is non-actionable → no new session.

    The fix moves the new-work-selection block above the runtime
    preflight so the cached canonical drives the CREATE path regardless
    of what the runtime classifier would have inferred.
    """

    def setUp(self) -> None:
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT.clear()

    def _seed_canonical(self, *, canonical_prompt: str, scope_key: tuple) -> None:
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = {
            "candidates": (
                {
                    "session_id": "old-1",
                    "title": "stale candidate",
                    "score": 0.4,
                    "thread_id": None,
                    "forum_thread_id": None,
                    "task_type": "research",
                },
            ),
            "canonical_prompt": canonical_prompt,
        }

    def _route(
        self,
        *,
        message,
        intake_fn,
        kickoff_fn,
        list_sessions_fn,
        research_loop_fn=None,
    ):
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError(
                        "conversation_fn must NOT run when the "
                        "clarification-create branch handles the message"
                    )
                ),
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=research_loop_fn,
                thread_continuation_fn=AsyncMock(
                    side_effect=AssertionError(
                        "thread_continuation must NOT run when clarification "
                        "create takes precedence"
                    )
                ),
                list_sessions_fn=list_sessions_fn,
            )
        )

    def test_new_work_with_canonical_wins_over_runtime_preflight(self) -> None:
        # Recall-rich session list — preflight would happily classify
        # the bare phrase as CONTINUE_EXISTING_WORK and attempt a JOIN
        # via thread_continuation_fn. The clarification-create branch
        # must intercept first, drive intake_fn with the canonical,
        # and never invoke thread_continuation.
        canonical = (
            "[Research] 결제 모듈 멱등성 검증 흐름을 백엔드에 추가하고 "
            "관련 회귀 시나리오 정리"
        )
        scope_key = (111, 9090)
        self._seed_canonical(canonical_prompt=canonical, scope_key=scope_key)

        sessions = [
            RuntimeFakeSession(
                session_id="ambig-1",
                prompt="결제 멱등성 점검",
                updated_at=_now(-15),
                extra={"research_pack": {"title": "결제"}},
            ),
            RuntimeFakeSession(
                session_id="ambig-2",
                prompt="결제 모듈 회귀",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "결제"}},
            ),
        ]

        intake_calls: dict = {}

        async def intake_fn(**kwargs):
            intake_calls.update(kwargs)
            return FakeIntakeResult(
                session=LegacyFakeSession(
                    session_id="new-pm", task_type="feature"
                ),
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )

        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=8081, message="kickoff"
            )
        )

        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 9090})()

        result = self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
            list_sessions_fn=lambda **_kw: sessions,
        )

        # 1) New session created with the canonical Research원문, not the
        #    routing-command phrase.
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "new-pm")
        self.assertEqual(intake_calls.get("prompt"), canonical)
        # 2) APPROVAL_ACTION ack template never reached the user.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertNotIn("승인 반영했습니다", sent)
        # 3) Cache cleared so a follow-up turn doesn't reuse the same
        #    canonical against an unrelated reply.
        from yule_orchestrator.discord import engineering_channel_router as router

        self.assertNotIn(scope_key, router._GATEWAY_CLARIFICATION_CONTEXT)

    def test_new_work_with_cache_but_missing_canonical_yields_error(self) -> None:
        # Cache present but canonical_prompt key is missing (older
        # entry). The router must refuse with a clear error instead of
        # falling through to APPROVAL_ACTION ack.
        from yule_orchestrator.discord import engineering_channel_router as router

        scope_key = (111, 9191)
        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = {
            "candidates": (
                {
                    "session_id": "stale",
                    "title": "stale",
                    "score": 0.1,
                    "thread_id": None,
                    "forum_thread_id": None,
                    "task_type": "research",
                },
            ),
            # canonical_prompt deliberately absent
        }

        intake_fn = AsyncMock(
            side_effect=AssertionError(
                "intake_fn must NOT run when canonical_prompt is missing"
            )
        )
        kickoff_fn = AsyncMock(
            side_effect=AssertionError(
                "kickoff_fn must NOT run when canonical_prompt is missing"
            )
        )

        message = FakeMessage(
            content="새 작업으로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 9191})()

        result = self._route(
            message=message,
            intake_fn=intake_fn,
            kickoff_fn=kickoff_fn,
            list_sessions_fn=lambda **_kw: [],
        )
        self.assertTrue(result.handled)
        # No new session.
        self.assertIsNone(result.session_id)
        # Clear refusal message — not the APPROVAL_ACTION ack.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("원문 task 본문을 찾지 못했어요", sent)
        self.assertNotIn("승인 반영했습니다", sent)
        # Cache cleared.
        self.assertNotIn(scope_key, router._GATEWAY_CLARIFICATION_CONTEXT)


class CanonicalPromptVsCommandOnlyAppendTests(unittest.TestCase):
    """User-spec regression: when the user replies with an explicit
    `기존 세션 <id>` after a clarification, the append payload must be
    the cached canonical prompt — not the routing-command phrase.

    Distinct from the numeric-pick path because explicit-session-id
    routing does NOT go through ``_handle_clarification_selection`` —
    it falls through to ``decide_routing`` which parses the explicit
    session id from the user's reply directly. The canonical-rewrite
    has to fire after conversation_fn but before
    ``_handle_join_or_append`` so the regex-match still fires while
    the append payload still gets canonical.
    """

    def setUp(self) -> None:
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()
        from yule_orchestrator.discord import engineering_channel_router as router

        router._GATEWAY_CLARIFICATION_CONTEXT.clear()

    def _seed_session(self, session_id: str, *, prompt: str = "이전 작업"):
        from datetime import datetime
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )

        now = datetime(2026, 5, 6)
        session = WorkflowSession(
            session_id=session_id,
            prompt=prompt,
            task_type="research",
            state=WorkflowState.APPROVED,
            created_at=now,
            updated_at=now,
            thread_id=7070,
        )
        save_session(session)
        return session

    def test_explicit_session_id_reply_appends_canonical(self) -> None:
        from yule_orchestrator.discord import engineering_channel_router as router

        # Both decide_routing's explicit-session branch and the runtime
        # preflight's recall need an actual cached session so the
        # lookup resolves to JOIN; without it the routing falls through
        # to ASK and the continuation_fn never runs.
        seeded = self._seed_session("abc12345", prompt="이전 작업 이전 결제")

        canonical = (
            "[Research] 결제 멱등성 백엔드 추가 + qa 회귀 시나리오 — 운영 흐름 포함"
        )
        scope_key = (111, 9696)
        router._GATEWAY_CLARIFICATION_CONTEXT[scope_key] = {
            "candidates": (),
            "canonical_prompt": canonical,
        }

        captured: dict = {}

        async def continuation_fn(**kwargs):
            captured.update(kwargs)
            return EngineeringThreadContinuation(
                session=LegacyFakeSession(
                    session_id="abc12345", task_type="research"
                ),
                thread_id=7070,
                message="기존 thread에 이어 붙였습니다.",
            )

        intake_fn = AsyncMock(
            side_effect=AssertionError("intake must NOT run on explicit-id pick")
        )
        kickoff_fn = AsyncMock(
            side_effect=AssertionError("kickoff must NOT run on explicit-id pick")
        )

        async def conversation_fn(**_kwargs):
            return EngineeringConversationOutcome(
                content="ack",
                confirmed=False,  # explicit-id reply isn't pre-confirmed
                intake_prompt=None,
            )

        message = FakeMessage(
            content="기존 세션 abc12345 로 이어가",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )
        message.author = type("A", (), {"id": 9696})()

        _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=continuation_fn,
                list_sessions_fn=lambda **_kw: [seeded],
            )
        )
        # The append payload must be the canonical Research원문 — not
        # "기존 세션 abc12345 로 이어가". Routing still parsed the
        # explicit session id from the user's reply via decide_routing.
        self.assertEqual(captured.get("prompt"), canonical)
        # Canonical cache cleared after the consume.
        self.assertNotIn(scope_key, router._GATEWAY_CLARIFICATION_CONTEXT)

    def test_command_only_session_does_not_resurface_as_candidate(self) -> None:
        # User-spec regression: a session whose .prompt is itself a
        # command-only phrase ("새 작업으로 진행") must not score 1.0
        # against an inbound command-only confirm. The scoring function
        # already drops command-only prompts from the matchable fields,
        # so the new inbound prompt finds no overlap and routes to
        # CREATE (or ASK with empty candidates) rather than JOINing the
        # zombie row.
        from yule_orchestrator.agents.routing import (
            _COMMAND_ONLY_PROMPTS,
            decide_routing,
            is_command_only_prompt,
        )

        # Sanity: confirm "새 작업으로 진행" is recognised as command-only
        # so the fixture is meaningful.
        self.assertTrue(is_command_only_prompt("새 작업으로 진행"))

        zombie = self._seed_session("zombie01", prompt="새 작업으로 진행")
        decision = decide_routing(
            prompt="새 작업으로 진행",
            open_sessions=(zombie,),
        )
        # Either CREATE or ASK with no high-confidence candidate — what
        # we MUST NOT see is JOIN against the zombie row.
        self.assertNotEqual(decision.matched_session_id, "zombie01")
        # And the candidate summary's score must not be 1.0 even if the
        # zombie surfaces as a low-confidence option.
        for cand in decision.candidate_summaries:
            if cand.session_id == "zombie01":
                self.assertLess(cand.score, 1.0)


if __name__ == "__main__":
    unittest.main()
