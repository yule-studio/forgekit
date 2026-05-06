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


if __name__ == "__main__":
    unittest.main()
