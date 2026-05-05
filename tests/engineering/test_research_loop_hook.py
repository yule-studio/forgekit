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
    from yule_orchestrator.discord import bot as bot_module
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"discord bot module unavailable: {exc}")

from yule_orchestrator.discord.engineering_channel_router import (
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
