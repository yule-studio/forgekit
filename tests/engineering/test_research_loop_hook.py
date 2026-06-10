"""Integration tests for the bot-level research loop hook.

Covers the pure-Python helpers in ``discord/bot.py`` that turn a
``ResearchLoopOutcome`` and a forum publication result into the
``EngineeringResearchLoopReport`` the router emits to Discord. We avoid
booting a real Discord client by exercising the helpers directly with
hand-built dummy outcomes.

Importing ``discord/bot.py`` requires ``discord.py``; if it is missing
the whole module is skipped so the unit-test suite stays portable.
"""

from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import unittest
from dataclasses import dataclass, field
from typing import Optional, Sequence

try:  # pragma: no cover - environment guard
    from yule_engineering.discord import bot as bot_module
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"discord bot module unavailable: {exc}")

from yule_engineering.discord.engineering_channel_router import (
    EngineeringResearchLoopReport,
)


# ---------------------------------------------------------------------------
# Dummy stand-ins (we do not import the real workflow / forum dataclasses to
# stay isolated from upstream signature drift; the helpers under test only
# poke at attribute names).
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    role_sequence: Sequence[str] = ()
    task_type: str = "unknown"
    executor_role: Optional[str] = None


@dataclass
class _Assignment:
    role: str
    actions: Sequence[str] = ()
    is_executor: bool = False


@dataclass
class _Outcome:
    session: _Session = field(default_factory=_Session)
    assignments: Sequence[_Assignment] = ()
    insufficient: bool = False


@dataclass
class _ThreadOutcome:
    posted: bool = True
    thread_id: Optional[int] = 7777
    thread_url: Optional[str] = "https://discord.com/threads/7777"
    error: Optional[str] = None
    fallback_markdown: Optional[str] = None


@dataclass
class _CommentOutcome:
    posted: bool = True
    error: Optional[str] = None


@dataclass
class _PublishOutcome:
    thread: Optional[_ThreadOutcome] = None
    role_comments: dict = field(default_factory=dict)
    decision_comment: Optional[_CommentOutcome] = None
    skipped_reason: Optional[str] = None
    kickoff_comment: Optional[_CommentOutcome] = None


def _outcome_with_designer_landing() -> _Outcome:
    return _Outcome(
        session=_Session(
            role_sequence=("tech-lead", "product-designer", "frontend-engineer"),
            task_type="landing-page",
            executor_role="frontend-engineer",
        ),
        assignments=(
            _Assignment(role="frontend-engineer", actions=("hero 구현",), is_executor=True),
        ),
    )


class FormatResearchHintsForOutcomeTestCase(unittest.TestCase):
    def test_returns_empty_when_role_sequence_is_empty(self) -> None:
        outcome = _Outcome(session=_Session(role_sequence=(), task_type="unknown"))
        self.assertEqual(bot_module._format_research_hints_for_outcome(outcome), "")

    def test_emits_per_role_lines_when_session_has_role_sequence(self) -> None:
        text = bot_module._format_research_hints_for_outcome(_outcome_with_designer_landing())
        self.assertIn("**역할별 자료 가이드**", text)
        self.assertIn("`product-designer`", text)
        self.assertIn("`frontend-engineer`", text)
        self.assertIn("image_reference", text)

    def test_no_session_attribute_returns_empty(self) -> None:
        class _Bare:
            pass

        self.assertEqual(bot_module._format_research_hints_for_outcome(_Bare()), "")


class ResearchLoopReportFromPublishTestCase(unittest.TestCase):
    def test_successful_publish_appends_role_hints_to_status_message(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            role_comments={"product-designer": _CommentOutcome(), "frontend-engineer": _CommentOutcome()},
            decision_comment=_CommentOutcome(),
        )

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        self.assertIsInstance(report, EngineeringResearchLoopReport)
        self.assertEqual(report.forum_thread_id, 7777)
        self.assertIn("운영-리서치 forum 게시 완료", report.forum_status_message)
        self.assertIn("`product-designer`", report.forum_status_message)
        self.assertIn("우선 자료:", report.forum_status_message)
        self.assertIn(
            "실행 후보 `frontend-engineer` 작업 1건 배정 완료",
            report.forum_status_message,
        )

    def test_publish_skipped_returns_skip_message_without_hints(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(thread=None, skipped_reason="insufficient research")

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        self.assertIn("forum 게시 생략", report.forum_status_message)
        self.assertNotIn("**역할별 자료 가이드**", report.forum_status_message or "")

    def test_publish_thread_failure_surfaces_fallback_markdown(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(
            thread=_ThreadOutcome(
                posted=False,
                error="discord api boom",
                fallback_markdown="# Research\n- foo",
            )
        )

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        self.assertIn("forum 게시 실패", report.forum_status_message)
        self.assertIn("# Research", report.forum_status_message)
        self.assertEqual(report.error, "discord api boom")


class MemberBotsModeSummaryTestCase(unittest.TestCase):
    """member-bots mode signals: the gateway only posted the
    ``[research-open:<session_id>]`` directive; per-role comments come
    from each member bot. Summary must reflect that — and never mention
    the gateway-mode "역할별 댓글 N건" line, which would look like a
    failure to operators."""

    def test_kickoff_posted_in_member_bots_mode_renders_directive_status(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=True),
        )

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        msg = report.forum_status_message or ""
        self.assertIn("운영-리서치 forum 게시 완료", msg)
        self.assertIn("모드: member-bots", msg)
        self.assertIn("open-call directive: 게시 완료", msg)
        self.assertIn("후속 댓글은 운영-리서치 thread", msg)
        # Gateway-mode wording must not appear.
        self.assertNotIn("역할별 댓글 0건", msg)
        self.assertNotIn("tech-lead 종합 미기록", msg)
        # Mode metadata reaches the report fields too.
        self.assertEqual(report.forum_comment_mode, "member-bots")
        self.assertTrue(report.kickoff_posted)
        self.assertIsNone(report.kickoff_error)

    def test_kickoff_failed_in_member_bots_mode_surfaces_reason(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=False, error="rate limit 503"),
        )

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        msg = report.forum_status_message or ""
        self.assertIn("모드: member-bots", msg)
        self.assertIn("open-call directive: 게시 실패", msg)
        self.assertIn("rate limit 503", msg)
        # Gateway-mode wording must still not leak.
        self.assertNotIn("역할별 댓글", msg)
        self.assertEqual(report.forum_comment_mode, "member-bots")
        self.assertFalse(report.kickoff_posted)
        self.assertEqual(report.kickoff_error, "rate limit 503")

    def test_gateway_mode_keeps_role_comment_summary(self) -> None:
        outcome = _outcome_with_designer_landing()
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            role_comments={
                "product-designer": _CommentOutcome(),
                "frontend-engineer": _CommentOutcome(),
            },
            decision_comment=_CommentOutcome(),
            # No kickoff_comment → gateway mode.
        )

        report = bot_module._research_loop_report_from_publish(outcome, publish)

        msg = report.forum_status_message or ""
        self.assertIn("역할별 댓글 2건", msg)
        self.assertIn("tech-lead 종합 기록", msg)
        # member-bots-only wording must not appear in gateway mode.
        self.assertNotIn("모드: member-bots", msg)
        self.assertNotIn("open-call directive", msg)
        self.assertEqual(report.forum_comment_mode, "gateway")
        self.assertIsNone(report.kickoff_posted)
        self.assertIsNone(report.kickoff_error)


class PersistForumCommentModeTestCase(unittest.TestCase):
    """``_persist_forum_comment_mode_to_session`` writes member-bots
    vs gateway mode signals into ``session.extra`` so the status
    diagnostic responder can describe the live setup later. Uses real
    WorkflowSession + isolated cache to round-trip through SQLite."""

    def setUp(self) -> None:  # noqa: D401
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover - bootstrap path
            from _helpers import isolate_cache_for_test  # type: ignore
        isolate_cache_for_test(self)

        from yule_engineering.agents.workflow_state import (
            WorkflowSession,
            WorkflowState,
            save_session,
        )
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        self._WorkflowSession = WorkflowSession
        self._WorkflowState = WorkflowState
        self._save_session = save_session
        self.session = WorkflowSession(
            session_id="abc123def456",
            prompt="Stripe pricing 페이지 hero copy",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            extra={"research_pack": {"title": "x"}},
        )
        save_session(self.session)

    def _reload(self):
        from yule_engineering.agents.workflow_state import load_session

        return load_session(self.session.session_id)

    def test_member_bots_kickoff_posted_writes_extra(self) -> None:
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=True),
        )
        bot_module._persist_forum_comment_mode_to_session(
            session=self.session, publish=publish
        )

        reloaded = self._reload()
        self.assertIsNotNone(reloaded)
        extra = dict(reloaded.extra)
        self.assertEqual(extra["forum_comment_mode"], "member-bots")
        self.assertTrue(extra["forum_kickoff_posted"])
        self.assertIsNone(extra["forum_kickoff_error"])

    def test_member_bots_kickoff_failed_writes_error(self) -> None:
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=False, error="rate limit 503"),
        )
        bot_module._persist_forum_comment_mode_to_session(
            session=self.session, publish=publish
        )

        reloaded = self._reload()
        extra = dict(reloaded.extra)
        self.assertEqual(extra["forum_comment_mode"], "member-bots")
        self.assertFalse(extra["forum_kickoff_posted"])
        self.assertEqual(extra["forum_kickoff_error"], "rate limit 503")

    def test_gateway_mode_writes_only_mode_key(self) -> None:
        publish = _PublishOutcome(
            thread=_ThreadOutcome(),
            role_comments={"product-designer": _CommentOutcome()},
            decision_comment=_CommentOutcome(),
            # No kickoff_comment → gateway mode.
        )
        bot_module._persist_forum_comment_mode_to_session(
            session=self.session, publish=publish
        )

        reloaded = self._reload()
        extra = dict(reloaded.extra)
        self.assertEqual(extra["forum_comment_mode"], "gateway")
        self.assertNotIn("forum_kickoff_posted", extra)
        self.assertNotIn("forum_kickoff_error", extra)

    def test_idempotent_overwrite_clears_stale_error(self) -> None:
        # First publish failed.
        publish_failed = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=False, error="initial error"),
        )
        bot_module._persist_forum_comment_mode_to_session(
            session=self.session, publish=publish_failed
        )
        # Retry succeeds — the second persist must clear the stale error.
        reloaded = self._reload()
        publish_ok = _PublishOutcome(
            thread=_ThreadOutcome(),
            kickoff_comment=_CommentOutcome(posted=True),
        )
        bot_module._persist_forum_comment_mode_to_session(
            session=reloaded, publish=publish_ok
        )

        final = self._reload()
        extra = dict(final.extra)
        self.assertTrue(extra["forum_kickoff_posted"])
        self.assertIsNone(extra["forum_kickoff_error"])


class FormatResearchForumDisabledStatusTestCase(unittest.TestCase):
    def test_disabled_status_includes_role_hints_when_sequence_known(self) -> None:
        outcome = _outcome_with_designer_landing()

        text = bot_module._format_research_forum_disabled_status(outcome)

        self.assertIn("forum env 미설정", text)
        self.assertIn("`product-designer`", text)
        self.assertIn("실행 후보 `frontend-engineer`", text)

    def test_disabled_status_omits_hints_when_sequence_empty(self) -> None:
        outcome = _Outcome(session=_Session(role_sequence=(), task_type="unknown"))

        text = bot_module._format_research_forum_disabled_status(outcome)

        self.assertIn("forum env 미설정", text)
        self.assertNotIn("**역할별 자료 가이드**", text)


if __name__ == "__main__":
    unittest.main()
