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
        # P0-J (#146): "왜 안 됐어?" / "왜 멈췄어" / "뭐가 막혔어" 는 더
        # 구체적인 BLOCKED_REASON_QUERY intent 로 분리됨. 본 test 는
        # 그 외 status_diagnostic 케이스만 보호.
        cases = (
            "운영 리서치는 안 열어?",
            "운영-리서치는 왜 안 열렸어?",
            "리서치 왜 실패했어?",
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

    def test_detects_expanded_phrases_added_in_polish(self) -> None:
        # New phrasings the user asked us to cover; each must NOT be
        # promoted to a new intake or read as a confirm phrase. P0-J
        # (#146): "뭐가 막혔어" / "왜 안 열렸어" 류는 BLOCKED_REASON_QUERY
        # 로 더 구체화 — 별도 test 에서 검증.
        cases = (
            "어떻게 됐어?",
            "어떻게 되고 있어?",
            "진행상황 좀",
            "어디까지 갔어?",
            "운영-리서치 왜 안 열려",
            "상태 체크 좀",
            "다시 한번 확인해줘",
            "옵시디언 왜 안 들어갔어",
            "리서치 왜 실패했어",
            "what happened with the research?",
            "status check please",
            "where are we",
        )
        for text in cases:
            with self.subTest(text=text):
                intent = detect_engineering_intent(text)
                self.assertEqual(intent.intent_id, STATUS_DIAGNOSTIC, text)

    def test_force_new_work_phrase_is_not_status_diagnostic(self) -> None:
        # "새 작업으로 진행" is the explicit override into a fresh
        # session — must remain a confirmation, not a status query.
        from yule_orchestrator.discord.engineering_conversation import (
            CONFIRM_INTAKE,
        )

        intent = detect_engineering_intent("새 작업으로 진행")
        self.assertEqual(intent.intent_id, CONFIRM_INTAKE)

    def test_typical_intake_request_remains_intake(self) -> None:
        # Typical fresh ask should still flow to the intake-candidate
        # branch — not get hijacked by a generic phrase like "어떻게".
        from yule_orchestrator.discord.engineering_conversation import (
            TASK_INTAKE_CANDIDATE,
        )

        intent = detect_engineering_intent(
            "Stripe pricing page 자료 찾아서 hero 카피 정리해줘"
        )
        self.assertEqual(intent.intent_id, TASK_INTAKE_CANDIDATE)

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

    def test_member_bots_mode_with_kickoff_posted(self) -> None:
        # When the publisher set member-bots mode and the open-call
        # directive landed cleanly, the diagnostic must say so and
        # point operators to the forum thread for actual role comments.
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("모드: member-bots", body)
        self.assertIn("open-call directive: 게시 완료", body)
        self.assertIn("후속 댓글은 운영-리서치 thread", body)

    def test_member_bots_mode_with_kickoff_failed_surfaces_reason(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": False,
                "forum_kickoff_error": "rate limit 503",
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("모드: member-bots", body)
        self.assertIn("open-call directive: 게시 실패", body)
        self.assertIn("rate limit 503", body)

    def test_gateway_mode_describes_gateway_comment_path(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "gateway",
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("모드: gateway", body)
        # member-bots-only wording must not appear in gateway mode.
        self.assertNotIn("open-call directive", body)
        self.assertNotIn("멤버 봇이 자기 계정으로", body)

    def test_role_turns_section_lists_recorded_role_activity(self) -> None:
        # Phase B: when member bots record their activity onto
        # session.extra["role_turns"], the diagnostic responder must
        # surface each role's status / kind / posted_at and any error.
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "research_open_call_posted": True,
                "role_turns": {
                    "ai-engineer": {
                        "status": "posted",
                        "kind": "open",
                        "posted_at": "2026-05-06T10:00:00+09:00",
                    },
                    "qa-engineer": {
                        "status": "error",
                        "kind": "turn",
                        "posted_at": "2026-05-06T10:01:00+09:00",
                        "error": "discord 5xx",
                    },
                },
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("역할 활동 기록", body)
        self.assertIn("ai-engineer", body)
        self.assertIn("posted (open", body)
        self.assertIn("qa-engineer", body)
        self.assertIn("error (turn", body)
        self.assertIn("discord 5xx", body)

    def test_research_open_call_keys_take_precedence_over_legacy(self) -> None:
        # Phase B canonical keys (research_open_call_*) must override
        # the legacy forum_kickoff_* keys when both are present so the
        # diagnostic always reflects the latest writer's intent.
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "research_open_call_posted": False,
                "research_open_call_error": "rate limit 503",
                # Legacy keys say "ok" — must be ignored.
                "forum_kickoff_posted": True,
                "forum_kickoff_error": None,
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("open-call directive: 게시 실패", body)
        self.assertIn("rate limit 503", body)


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
        # P0-J (#146): "왜 안 됐어?" 는 BLOCKED_REASON_QUERY 로 분리됨.
        # 본 test 는 status_diagnostic 분기 자체 — pure status phrase 로
        # 유지하면서 loader 없을 때 fall-back 동작 검증.
        response = build_engineering_conversation_response(
            "지금 뭐 하는 중이야?",
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


class MemberBotPhraseDetectionTests(unittest.TestCase):
    """Phase E expansion — explicit "멤버 봇" questions must route to
    the status diagnostic intent so the response can include the
    member-bot summary block instead of falling through to intake."""

    def test_korean_member_bot_phrasings_detected_as_status(self) -> None:
        cases = (
            "멤버 봇들은 뭐 하고 있어?",
            "멤버봇 진행 상황 알려줘",
            "역할 봇들 어떻게 됐어?",
            "member bot 상태 확인",
        )
        for text in cases:
            with self.subTest(text=text):
                intent = detect_engineering_intent(text)
                self.assertEqual(intent.intent_id, STATUS_DIAGNOSTIC, text)


class DiagnosticSignalsAppearInResponseTests(unittest.TestCase):
    """The format function must surface the structured signals so
    the operator sees "왜 멈췄는지" without re-deriving the rules."""

    def test_research_pack_without_open_call_appends_stale_signal(self) -> None:
        session = _session(extra={"research_pack": {"title": "x"}})
        body = format_status_diagnostic_response(session)
        self.assertIn("감지된 다음 단계:", body)
        self.assertIn("[STALE]", body)
        self.assertIn("research_pack 있음", body)

    def test_obsidian_pending_approval_renders_blocked_tag(self) -> None:
        session = _session(
            write_requested=True,
            write_blocked_reason="작성 승인이 필요합니다",
            extra={
                "research_pack": {"title": "x"},
                "research_synthesis": {"summary": "ok"},
            },
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("[BLOCKED]", body)
        self.assertIn("Obsidian write 승인 대기", body)
        self.assertIn("yule engineer approve", body)

    def test_obsidian_write_failed_renders_failed_tag(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_synthesis": {"summary": "ok"},
                "obsidian_write_error": "Permission denied at /vault",
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("[FAILED]", body)
        self.assertIn("Obsidian write 실패", body)
        self.assertIn("Permission denied", body)

    def test_member_bot_question_appends_member_bot_summary(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
                "team_conversation": {"played_roles": ["tech-lead"]},
            }
        )
        # Simulate the gateway reaching format with the flag on.
        body = format_status_diagnostic_response(
            session, is_member_bot_question=True
        )
        self.assertIn("멤버 봇 진행 상태", body)
        self.assertIn("응답한 역할(1)", body)


class StatusQueryNoMemberBotPathDoesNotLeakBlockTests(unittest.TestCase):
    """Calling without the flag must NOT include the member-bot block —
    keeps the regular '지금 뭐 하는 중?' answer compact."""

    def test_default_call_omits_member_bot_block(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertNotIn("멤버 봇 진행 상태", body)


if __name__ == "__main__":
    unittest.main()
