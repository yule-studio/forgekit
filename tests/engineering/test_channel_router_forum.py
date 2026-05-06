"""Engineering channel router — forum / research-loop hook.

Covers: status-message forwarding from research_loop_fn to chat,
insufficient + failure handling, the default research-loop helper,
member-bots vs gateway forum modes, and the research-turn dispatch
protocol used by member-bot runtimes.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeIntakeResult,
    FakeMessage,
    FakeMessageWithAttachments,
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


# Test stub matching ``research_collector.CollectionOutcome.mode`` shape.
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


# ---------------------------------------------------------------------------
# Research loop wiring through route_engineering_message.
# ---------------------------------------------------------------------------


class ResearchLoopHookTests(unittest.TestCase):
    """The router must surface research_loop_fn output to the user."""

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

    def test_research_loop_status_message_is_sent(self) -> None:
        message = FakeMessageWithAttachments(
            content="이대로 진행해 주세요",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessageWithAttachments(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessageWithAttachments(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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
        message = FakeMessage(
            content="이번 작업 우선순위 좀 정리해줘",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
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


# ---------------------------------------------------------------------------
# Default research-loop helper (gateway + member-bot modes).
# ---------------------------------------------------------------------------


class DefaultResearchLoopTests(unittest.TestCase):
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
        self.assertIn("deliberation 실패", report.error or "")


# ---------------------------------------------------------------------------
# Centralised label helpers reused across conversation/forum surfaces.
# ---------------------------------------------------------------------------


class CentralisedLabelTests(unittest.TestCase):
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
        from yule_orchestrator.agents.research_collector import pretty_source_type
        from yule_orchestrator.agents.research_pack import SourceType

        self.assertEqual(pretty_source_type(SourceType.OFFICIAL_DOCS), "공식 문서")
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


# ---------------------------------------------------------------------------
# Forum-side research turn protocol (member-bot dispatcher contract).
# ---------------------------------------------------------------------------


class ResearchTurnKickoffInForumTests(unittest.TestCase):
    """member-bots mode posts one open-call directive into the forum thread."""

    def test_member_bots_mode_posts_open_call_directive_only(self) -> None:
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        session = WorkflowSession(
            session_id="sess-9",
            prompt="hero 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
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


class ResearchTurnProtocolTests(unittest.TestCase):
    """Protocol-level tests live with the forum suite so they run alongside
    the wiring they enable."""

    def test_parse_marker_extracts_session_and_role(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            parse_research_dispatch_marker,
        )

        self.assertEqual(
            parse_research_dispatch_marker(
                "preamble [research-turn:abc123 ai-engineer] tail"
            ),
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
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from yule_orchestrator.discord.engineering_team_runtime import (
            deliberation_research_role_sequence,
        )

        session = WorkflowSession(
            session_id="x",
            prompt="x",
            task_type="x",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
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
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )
        from yule_orchestrator.discord.engineering_team_runtime import (
            DEFAULT_RESEARCH_ROLE_SEQUENCE,
            deliberation_research_role_sequence,
        )

        session = WorkflowSession(
            session_id="x",
            prompt="x",
            task_type="x",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
            role_sequence=(),
        )
        seq = deliberation_research_role_sequence(session)
        self.assertEqual(seq, DEFAULT_RESEARCH_ROLE_SEQUENCE)


class HandleResearchTurnMessageTests(unittest.TestCase):
    """Member bots only post when the marker targets their role."""

    def setUp(self) -> None:
        # Reset the process-local duplicate-suppression set so tests in
        # earlier modules don't leak ``(role, session, kind)`` markers
        # and trick handle_research_turn_message into a no-op return.
        from yule_orchestrator.discord.engineering_team_runtime import (
            reset_handled_turns_for_tests,
        )

        reset_handled_turns_for_tests()
        self.addCleanup(reset_handled_turns_for_tests)

    def _session(self):
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )

        return WorkflowSession(
            session_id="sess-1",
            prompt="hero 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
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


# ---------------------------------------------------------------------------
# Phase B — member-bots summary + open-call status persistence + role policy
# ---------------------------------------------------------------------------


class MemberBotsSummaryAndPersistenceTests(unittest.TestCase):
    """Phase B regression: member-bots mode must (1) produce a summary
    that does NOT mention "역할별 댓글 N건" gateway-mode wording, and
    (2) persist the kickoff status onto session.extra so the diagnostic
    responder can describe the live setup."""

    def _session_factory(self):
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )

        return WorkflowSession(
            session_id="sess-mb",
            prompt="hero 정리",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
            role_sequence=("tech-lead", "ai-engineer"),
        )

    def _publisher(self, *, posted: bool, thread_id=7777, thread_url=None):
        async def forum_publisher(**_):
            class _Outcome:
                pass

            outcome = _Outcome()
            outcome.posted = posted
            outcome.thread_id = thread_id
            outcome.thread_url = thread_url
            outcome.error = None
            return outcome

        return forum_publisher

    def test_summary_drops_role_comment_count_in_member_bots_mode(self) -> None:
        """The default research loop's summary must not include
        ``역할별 댓글 N건`` wording in member-bots mode — that line is
        gateway-mode only and confuses operators when each member bot
        is responsible for the role comment."""

        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        forum_posts: list[tuple[int, str]] = []

        async def post_to_forum_thread(thread_id, content):
            forum_posts.append((thread_id, content))

        report = _run(
            make_default_research_loop(
                session=self._session_factory(),
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                role_for_research="engineering-agent/tech-lead",
                thread_id=12345,
                forum_publisher=self._publisher(posted=True),
                post_to_forum_thread=post_to_forum_thread,
                forum_comment_mode="member-bots",
            )
        )

        msg = report.forum_status_message or ""
        self.assertIn("운영-리서치 forum 게시 완료", msg)
        self.assertIn("모드: member-bots", msg)
        self.assertIn("open-call directive: 게시 완료", msg)
        self.assertIn("후속 댓글은 운영-리서치 thread", msg)
        # Gateway-mode-only wording must not leak into member-bots mode.
        self.assertNotIn("역할별 댓글 0건", msg)
        self.assertNotIn("역할별 댓글", msg)
        # Mode metadata reaches the report fields too.
        self.assertEqual(report.forum_comment_mode, "member-bots")
        self.assertTrue(report.kickoff_posted)
        self.assertIsNone(report.kickoff_error)

    def test_kickoff_failure_in_member_bots_mode_surfaces_reason(self) -> None:
        from yule_orchestrator.discord.engineering_channel_router import (
            make_default_research_loop,
        )

        async def post_to_forum_thread(thread_id, content):
            raise RuntimeError("rate limit 503")

        report = _run(
            make_default_research_loop(
                session=self._session_factory(),
                message_text="prompt",
                attachments=(),
                channel=None,
                collection_outcome=_StubCollectionOutcome(),
                research_pack="<<pack>>",
                role_for_research="engineering-agent/tech-lead",
                thread_id=12345,
                forum_publisher=self._publisher(posted=True),
                post_to_forum_thread=post_to_forum_thread,
                forum_comment_mode="member-bots",
            )
        )

        self.assertEqual(report.forum_comment_mode, "member-bots")
        self.assertFalse(report.kickoff_posted)
        self.assertIn("rate limit 503", report.kickoff_error or "")
        # Error path must not regress the "역할별 댓글 N건" guard.
        self.assertNotIn(
            "역할별 댓글", report.forum_status_message or ""
        )

    def test_persist_research_forum_status_writes_canonical_keys(self) -> None:
        """``persist_research_forum_status`` writes the Phase B keys
        (research_open_call_*, forum_comment_mode, research_forum_thread_id)
        plus legacy aliases for backward compat."""

        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover - bootstrap path
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            load_session,
            save_session,
        )
        from yule_orchestrator.discord.engineering_channel_router import (
            persist_research_forum_status,
            EngineeringResearchLoopReport,
        )

        now = datetime(2026, 4, 30)
        session = WorkflowSession(
            session_id="sess-persist",
            prompt="x",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
        )
        save_session(session)

        report = EngineeringResearchLoopReport(
            forum_status_message="ok",
            forum_thread_id=4242,
            forum_thread_url="https://discord.test/4242",
            forum_comment_mode="member-bots",
            kickoff_posted=True,
            kickoff_error=None,
        )
        persist_research_forum_status(session=session, report=report)

        reloaded = load_session("sess-persist")
        self.assertIsNotNone(reloaded)
        extra = dict(reloaded.extra)
        self.assertEqual(extra.get("forum_comment_mode"), "member-bots")
        self.assertEqual(extra.get("research_forum_thread_id"), 4242)
        self.assertEqual(
            extra.get("research_forum_thread_url"), "https://discord.test/4242"
        )
        self.assertTrue(extra.get("research_open_call_posted"))
        self.assertIsNone(extra.get("research_open_call_error"))
        # Legacy aliases stay in sync for back-compat with existing
        # diagnostic tests.
        self.assertTrue(extra.get("forum_kickoff_posted"))
        self.assertIsNone(extra.get("forum_kickoff_error"))

    def test_persist_research_forum_status_records_kickoff_error(self) -> None:
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover - bootstrap path
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            load_session,
            save_session,
        )
        from yule_orchestrator.discord.engineering_channel_router import (
            persist_research_forum_status,
            EngineeringResearchLoopReport,
        )

        now = datetime(2026, 4, 30)
        session = WorkflowSession(
            session_id="sess-fail",
            prompt="x",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
        )
        save_session(session)

        report = EngineeringResearchLoopReport(
            forum_thread_id=99,
            forum_comment_mode="member-bots",
            kickoff_posted=False,
            kickoff_error="forum kickoff 게시 실패: rate limit",
        )
        persist_research_forum_status(session=session, report=report)

        reloaded = load_session("sess-fail")
        extra = dict(reloaded.extra)
        self.assertFalse(extra.get("research_open_call_posted"))
        self.assertIn("rate limit", extra.get("research_open_call_error") or "")
        self.assertEqual(extra.get("forum_comment_mode"), "member-bots")


class RoleRuntimePrefaceTests(unittest.TestCase):
    """Phase B role-runtime MVP: the open-call handler must produce a
    take whose body covers all 5 sections (이해한 작업 / 역할 관점의
    판단 / 참고 자료 / 리스크 / 다음 행동) and threads the role policy
    through the runtime input.

    The deterministic deliberation render already covers 관점 / 근거 /
    리스크 / 다음 행동, so the preface only needs to add 이해한 작업 +
    role policy stamping.
    """

    def setUp(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            reset_handled_turns_for_tests,
        )

        reset_handled_turns_for_tests()
        self.addCleanup(reset_handled_turns_for_tests)

    def _session(self):
        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
        )

        return WorkflowSession(
            session_id="sess-rt",
            prompt=(
                "운영-리서치 게시 흐름에서 멤버 봇이 진짜 자기 역할로 "
                "응답했는지 확인할 수 있는 진단 흐름을 설계해줘"
            ),
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30),
            updated_at=datetime(2026, 4, 30),
            # Include the engineering roles the runtime tests exercise so
            # the open-call handler accepts them as participants.
            role_sequence=(
                "tech-lead",
                "ai-engineer",
                "backend-engineer",
                "qa-engineer",
            ),
        )

    def test_open_call_take_includes_runtime_preface_and_role_policy(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sess-rt]",
            session_loader=lambda _sid: self._session(),
        )

        self.assertIsNotNone(outcome)
        msg = outcome.message
        # 5-section contract — 이해한 작업 / 판단 근거 / 참고 자료 (or
        # research_pack) / 리스크 / 다음 행동. The deterministic role
        # take rendered by deliberation_role_turn already gives 근거 /
        # 리스크 / 다음 행동; the runtime preface contributes 이해한
        # 작업 + role-policy-driven 판단 근거 line.
        self.assertIn("역할 runtime 결과", msg)
        self.assertIn("이해한 작업:", msg)
        self.assertIn("내 역할 관점의 판단 근거", msg)
        self.assertIn("리스크", msg)
        self.assertIn("다음 행동", msg)
        # Role policy short_name is stamped through the preface so a
        # future runtime can swap to an LLM-backed take without losing
        # the policy provenance.
        self.assertIn("ai-engineer", msg)
        # Existing open-call memo footer must still be present so the
        # forum thread still distinguishes autonomous takes from
        # gateway-driven turns.
        self.assertIn("자율 조사 메모", msg)

    def test_role_runtime_input_carries_role_policy(self) -> None:
        """The runtime input fed into ``run_runtime_loop`` must carry the
        role policy. Patches the loop entry to capture the input."""

        from unittest.mock import patch

        captured: dict = {}

        def _capture(input_, **_kwargs):
            captured["input"] = input_

            class _NoopResult:
                error = None

            return _NoopResult()

        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        with patch(
            "yule_orchestrator.agents.runtime.run_runtime_loop",
            side_effect=_capture,
        ):
            outcome = handle_research_turn_message(
                role="backend-engineer",
                text="[research-open:sess-rt]",
                session_loader=lambda _sid: self._session(),
            )

        self.assertIsNotNone(outcome)
        runtime_input = captured.get("input")
        self.assertIsNotNone(runtime_input)
        self.assertEqual(runtime_input.role_id, "engineering-agent/backend-engineer")
        # Policy carries the canonical short_name + memory filter so the
        # take preface can stamp them deterministically.
        policy = (runtime_input.policy or {}).get("role_policy") or {}
        self.assertEqual(policy.get("short_name"), "backend-engineer")
        self.assertEqual(policy.get("memory_role_filter"), "backend-engineer")
        self.assertTrue(policy.get("description"))

    def test_runtime_failure_falls_back_to_deterministic_render(self) -> None:
        """Runtime errors must not block the post — the deterministic
        role-turn render still serves as the fallback body."""

        from unittest.mock import patch

        from yule_orchestrator.discord.engineering_team_runtime import (
            handle_research_turn_message,
        )

        with patch(
            "yule_orchestrator.agents.runtime.run_runtime_loop",
            side_effect=RuntimeError("runtime down"),
        ):
            outcome = handle_research_turn_message(
                role="ai-engineer",
                text="[research-open:sess-rt]",
                session_loader=lambda _sid: self._session(),
            )

        self.assertIsNotNone(outcome)
        # Deterministic role take's 4-section body must still be there.
        self.assertIn("**[ai-engineer]**", outcome.message)
        self.assertIn("리스크", outcome.message)
        self.assertIn("자율 조사 메모", outcome.message)


class RecordRoleTurnEventTests(unittest.TestCase):
    """``record_role_turn_event`` must persist a role-keyed event on
    session.extra and never raise from caller-facing code paths."""

    def setUp(self) -> None:
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        from yule_orchestrator.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )

        now = datetime(2026, 4, 30)
        self._WorkflowSession = WorkflowSession
        self._WorkflowState = WorkflowState
        self.session = WorkflowSession(
            session_id="sess-evt",
            prompt="hero 정리",
            task_type="research",
            state=WorkflowState.APPROVED,
            created_at=now,
            updated_at=now,
        )
        save_session(self.session)

    def _reload(self):
        from yule_orchestrator.agents.workflow_state import load_session

        return load_session("sess-evt")

    def test_posted_event_lands_in_role_turns(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            ROLE_TURN_KIND_OPEN,
            ROLE_TURN_STATUS_POSTED,
            record_role_turn_event,
        )

        record_role_turn_event(
            session_id="sess-evt",
            role="ai-engineer",
            kind=ROLE_TURN_KIND_OPEN,
            status=ROLE_TURN_STATUS_POSTED,
        )
        reloaded = self._reload()
        role_turns = dict((reloaded.extra or {}).get("role_turns") or {})
        self.assertIn("ai-engineer", role_turns)
        event = role_turns["ai-engineer"]
        self.assertEqual(event["status"], "posted")
        self.assertEqual(event["kind"], "open")
        self.assertIn("posted_at", event)
        self.assertNotIn("error", event)

    def test_error_event_records_reason(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            ROLE_TURN_KIND_TURN,
            ROLE_TURN_STATUS_ERROR,
            record_role_turn_event,
        )

        record_role_turn_event(
            session_id="sess-evt",
            role="qa-engineer",
            kind=ROLE_TURN_KIND_TURN,
            status=ROLE_TURN_STATUS_ERROR,
            error="Discord 5xx",
        )
        reloaded = self._reload()
        role_turns = dict((reloaded.extra or {}).get("role_turns") or {})
        self.assertEqual(role_turns["qa-engineer"]["status"], "error")
        self.assertEqual(role_turns["qa-engineer"]["error"], "Discord 5xx")

    def test_record_failure_is_silent(self) -> None:
        from yule_orchestrator.discord.engineering_team_runtime import (
            record_role_turn_event,
        )

        # No save_session for this id → load returns None → recorder
        # silently no-ops (no raise).
        record_role_turn_event(
            session_id="ghost",
            role="ai-engineer",
            kind="open",
            status="posted",
        )

    def test_repeated_event_overwrites_latest(self) -> None:
        """We keep history-light: latest event per role wins so the
        diagnostic surface stays compact."""

        from yule_orchestrator.discord.engineering_team_runtime import (
            record_role_turn_event,
        )

        record_role_turn_event(
            session_id="sess-evt",
            role="ai-engineer",
            kind="open",
            status="error",
            error="first",
        )
        record_role_turn_event(
            session_id="sess-evt",
            role="ai-engineer",
            kind="open",
            status="posted",
        )
        role_turns = dict((self._reload().extra or {}).get("role_turns") or {})
        self.assertEqual(role_turns["ai-engineer"]["status"], "posted")
        self.assertNotIn("error", role_turns["ai-engineer"])


if __name__ == "__main__":
    unittest.main()
