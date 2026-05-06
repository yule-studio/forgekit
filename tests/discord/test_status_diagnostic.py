"""Status / diagnostic intent for the engineering gateway.

When the user asks "운영 리서치는 안 열어?" / "왜 안 됐어?" / "지금 뭐 하는
중?" the gateway must NOT promote that to a new task intake. Instead it
should detect a status intent, read the latest open session via the
injected loader, and answer with the real session state.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState
from yule_orchestrator.discord.engineering_conversation import (
    STATUS_DIAGNOSTIC,
    build_engineering_conversation_response,
    detect_engineering_intent,
    format_status_diagnostic_response,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _session(**overrides: Any) -> WorkflowSession:
    base = dict(
        session_id="abc123def456",
        prompt="운영 리서치 검토 작업",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=datetime(2026, 5, 5, 10, 0),
        updated_at=datetime(2026, 5, 6, 9, 0),
        channel_id=1001,
        user_id=2002,
        thread_id=3003,
    )
    base.update(overrides)
    return WorkflowSession(**base)


class StatusDiagnosticIntentDetectionTests(unittest.TestCase):
    def test_detects_korean_research_status_questions(self) -> None:
        cases = (
            "운영 리서치는 안 열어?",
            "운영-리서치는 왜 안 열렸어?",
            "리서치 왜 실패했어?",
            "왜 안 됐어?",
            "왜 멈췄어",
            "지금 뭐 하는 중이야?",
            "현재 상태 알려줘",
            "진행 상황 어떻게 되고 있어?",
            "Obsidian 왜 안 들어갔어?",
            "포럼 왜 안 열렸어",
        )
        for text in cases:
            with self.subTest(text=text):
                intent = detect_engineering_intent(text)
                self.assertEqual(intent.intent_id, STATUS_DIAGNOSTIC, text)

    def test_does_not_treat_real_work_request_as_status_query(self) -> None:
        # Diagnostic detection must not over-fire; "왜 hero copy를 강조해야
        # 하는지 자료 정리해줘" is a research request, not a status check.
        intent = detect_engineering_intent(
            "hero copy를 왜 강조해야 하는지 자료를 정리해줘"
        )
        self.assertNotEqual(intent.intent_id, STATUS_DIAGNOSTIC)

    def test_confirmation_is_not_status_query(self) -> None:
        intent = detect_engineering_intent("이대로 진행")
        self.assertNotEqual(intent.intent_id, STATUS_DIAGNOSTIC)


class StatusDiagnosticResponseFormatterTests(unittest.TestCase):
    def test_no_session_returns_safe_no_open_session_message(self) -> None:
        body = format_status_diagnostic_response(None)
        self.assertIn("열린 engineering-agent 세션이 보이지 않아요", body)
        self.assertIn("session id", body)

    def test_renders_session_id_state_and_research_pack_present(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "Stripe pricing 검토"},
            },
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("`abc123def456`", body)
        self.assertIn("research_pack: 있음", body)
        self.assertIn("운영-리서치 forum: 아직 게시되지 않음", body)

    def test_distinguishes_forum_thread_present_vs_failure(self) -> None:
        success = _session(
            extra={
                "research_pack": {"title": "ok"},
                "research_forum_thread_id": 9911,
                "research_forum_thread_url": "https://discord.test/9911",
            }
        )
        body_ok = format_status_diagnostic_response(success)
        self.assertIn("게시됨", body_ok)
        self.assertIn("https://discord.test/9911", body_ok)

        failure = _session(
            extra={
                "research_pack": {"title": "x"},
                "forum_publish_error": (
                    "400 Bad Request 50035: message.content 4000자 초과"
                ),
            }
        )
        body_fail = format_status_diagnostic_response(failure)
        self.assertIn("게시 실패", body_fail)
        self.assertIn("4000자 초과", body_fail)

    def test_surfaces_research_loop_report_error(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_loop_report": {"error": "forum starter 게시 실패"},
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("research loop 오류", body)
        self.assertIn("forum starter", body)


class BuildResponseStatusQueryTests(unittest.TestCase):
    """End-to-end: status questions never set ready_to_intake and never
    trigger auto_collect — the response carries is_status_query=True so
    the channel router can short-circuit before decide_routing."""

    def test_status_query_returns_status_response_without_intake(self) -> None:
        loaded = _session(
            extra={
                "research_pack": {"title": "x"},
                "forum_publish_error": "starter 4000자 초과",
            }
        )
        called = {"loader": 0, "collector": 0}

        def loader() -> Any:
            called["loader"] += 1
            return loaded

        def fake_collector(*_args, **_kwargs) -> Any:
            called["collector"] += 1
            raise AssertionError("auto-collect must not run for status query")

        response = build_engineering_conversation_response(
            "운영 리서치는 안 열어?",
            author_user_id=2002,
            status_session_loader=loader,
            collector=fake_collector,
        )
        self.assertEqual(response.intent_id, STATUS_DIAGNOSTIC)
        self.assertTrue(response.is_status_query)
        self.assertFalse(response.ready_to_intake)
        self.assertFalse(response.needs_clarification)
        self.assertEqual(response.proposed_splits, ())
        self.assertIsNone(response.research_pack)
        self.assertIsNone(response.collection_outcome)
        self.assertEqual(called["loader"], 1)
        self.assertEqual(called["collector"], 0)
        self.assertIn("`abc123def456`", response.content)
        self.assertIn("starter 4000자 초과", response.content)

    def test_status_query_handles_loader_failure_gracefully(self) -> None:
        def loader() -> Any:
            raise RuntimeError("cache offline")

        response = build_engineering_conversation_response(
            "지금 뭐 하는 중이야?",
            status_session_loader=loader,
        )
        self.assertTrue(response.is_status_query)
        self.assertIn("열린 engineering-agent 세션이 보이지 않아요", response.content)

    def test_status_query_with_no_loader_falls_back_to_no_session(self) -> None:
        response = build_engineering_conversation_response(
            "왜 안 됐어?",
            status_session_loader=None,
        )
        self.assertTrue(response.is_status_query)
        self.assertEqual(response.intent_id, STATUS_DIAGNOSTIC)


class RouterStatusQueryShortCircuitTests(unittest.TestCase):
    """The channel router must NOT call decide_routing or intake_fn when
    the conversation layer flagged is_status_query. Otherwise "운영
    리서치는 안 열어?" would create a brand-new session and re-trigger
    the "1차 자료를 모아볼게요" template the user is complaining about."""

    def test_status_outcome_short_circuits_router(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            EngineeringConversationOutcome,
            EngineeringRouteContext,
            route_engineering_message,
        )

        sent: list[str] = []

        async def send_chunks(_channel: Any, text: str) -> None:
            sent.append(text)

        async def conversation_fn(**_: Any) -> EngineeringConversationOutcome:
            return EngineeringConversationOutcome(
                content="현재 운영-리서치 forum 게시 실패 상태입니다.",
                is_status_query=True,
            )

        def must_not_be_called(*_a: Any, **_kw: Any) -> Any:
            raise AssertionError("intake must not run for a status query")

        async def kickoff_must_not_be_called(**_: Any) -> Any:
            raise AssertionError("kickoff must not run for a status query")

        async def research_must_not_be_called(**_: Any) -> Any:
            raise AssertionError("research loop must not run for a status query")

        ctx = EngineeringRouteContext(
            intake_channel_id=1001,
            intake_channel_name="업무-접수",
        )
        message = SimpleNamespace(
            content="운영 리서치는 안 열어?",
            channel=SimpleNamespace(id=1001, name="업무-접수"),
            author=SimpleNamespace(id=2002),
            attachments=(),
        )
        result = _run(
            route_engineering_message(
                message=message,
                bot_user=SimpleNamespace(id=42),
                route_context=ctx,
                extract_prompt=lambda message, bot_user: message.content,
                conversation_fn=conversation_fn,
                intake_fn=must_not_be_called,
                thread_kickoff_fn=kickoff_must_not_be_called,
                send_chunks=send_chunks,
                research_loop_fn=research_must_not_be_called,
                thread_continuation_fn=None,
            )
        )

        self.assertTrue(result.handled)
        # decide_routing was never reached, so no routing_decision is set.
        self.assertIsNone(result.routing_decision)
        # No new session was created.
        self.assertIsNone(result.session_id)
        # The conversation status answer was the only message sent.
        self.assertEqual(len(sent), 1)
        self.assertIn("운영-리서치 forum 게시 실패", sent[0])


if __name__ == "__main__":
    unittest.main()
