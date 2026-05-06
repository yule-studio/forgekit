from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import os
import tempfile
import unittest

import yule_orchestrator.discord.commands as discord_commands
from yule_orchestrator.agents.review_loop import ReviewSeverity, ReviewSource


class DiscordCommandsTestCase(unittest.TestCase):
    def test_bind_discord_runtime_globals_sets_module_globals(self) -> None:
        fake_discord = object()
        fake_app_commands = object()
        sentinel = object()
        previous_discord = discord_commands.__dict__.get("discord", sentinel)
        previous_app_commands = discord_commands.__dict__.get("app_commands", sentinel)

        try:
            discord_commands._bind_discord_runtime_globals(
                discord_module=fake_discord,
                app_commands_module=fake_app_commands,
            )

            self.assertIs(discord_commands.__dict__["discord"], fake_discord)
            self.assertIs(discord_commands.__dict__["app_commands"], fake_app_commands)
        finally:
            if previous_discord is sentinel:
                discord_commands.__dict__.pop("discord", None)
            else:
                discord_commands.__dict__["discord"] = previous_discord

            if previous_app_commands is sentinel:
                discord_commands.__dict__.pop("app_commands", None)
            else:
                discord_commands.__dict__["app_commands"] = previous_app_commands

    def test_split_lines_or_semicolons_strips_bullets_and_separators(self) -> None:
        result = discord_commands._split_lines_or_semicolons(
            "- 카피 교체\n- CTA 색상\n;final consult"
        )
        self.assertEqual(result, ("카피 교체", "CTA 색상", "final consult"))

    def test_split_csv_drops_blanks(self) -> None:
        self.assertEqual(
            discord_commands._split_csv(" ui , copy ,, ux "),
            ("ui", "copy", "ux"),
        )

    def test_parse_review_severity_defaults_to_medium(self) -> None:
        self.assertEqual(
            discord_commands._parse_review_severity(None),
            ReviewSeverity.MEDIUM,
        )

    def test_parse_review_severity_rejects_unknown_value(self) -> None:
        with self.assertRaises(ValueError):
            discord_commands._parse_review_severity("urgent")

    def test_parse_review_source_defaults_to_user(self) -> None:
        self.assertEqual(
            discord_commands._parse_review_source(None),
            ReviewSource.USER,
        )

    def test_generate_feedback_id_has_expected_prefix(self) -> None:
        identifier = discord_commands._generate_feedback_id()
        self.assertTrue(identifier.startswith("fb-"))
        self.assertEqual(len(identifier), len("fb-") + 8)


class EngineerReviewSlashHelpersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev_db = os.environ.get("YULE_CACHE_DB_PATH")
        os.environ["YULE_CACHE_DB_PATH"] = os.path.join(self._tmp.name, "cache.sqlite3")

    def tearDown(self) -> None:
        if self._prev_db is None:
            os.environ.pop("YULE_CACHE_DB_PATH", None)
        else:
            os.environ["YULE_CACHE_DB_PATH"] = self._prev_db

    def _intake_session(self) -> str:
        orchestrator = discord_commands._engineer_orchestrator()
        result = orchestrator.intake(prompt="새 랜딩 hero 정리", write_requested=False)
        return result.session.session_id

    def test_run_engineer_review_records_feedback_and_returns_routing(self) -> None:
        session_id = self._intake_session()

        result = discord_commands._run_engineer_review(
            session_id=session_id,
            summary="hero 카피가 약하고 비주얼이 평이합니다",
            body=None,
            severity="high",
            categories="copy, visual",
            source="github_pr_review",
            file_paths="web/Hero.tsx",
            channel_id=42,
            thread_id=42,
            user_id=777,
            author_name="reviewer-1",
        )

        self.assertEqual(result.session.review_cycle, 1)
        self.assertEqual(result.routing.primary_role, "product-designer")
        self.assertTrue(result.feedback.feedback_id.startswith("fb-"))
        self.assertIn("리뷰 피드백 수신", result.message)

    def test_run_engineer_review_rejects_blank_summary(self) -> None:
        session_id = self._intake_session()
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_review(
                session_id=session_id,
                summary="   ",
                body=None,
                severity=None,
                categories=None,
                source=None,
                file_paths=None,
                channel_id=None,
                thread_id=None,
                user_id=None,
                author_name=None,
            )

    def test_run_engineer_review_reply_writes_progress_note(self) -> None:
        session_id = self._intake_session()
        intake = discord_commands._run_engineer_review(
            session_id=session_id,
            summary="copy 부족",
            body=None,
            severity=None,
            categories="copy",
            source=None,
            file_paths=None,
            channel_id=None,
            thread_id=None,
            user_id=777,
            author_name="reviewer-1",
        )

        reply = discord_commands._run_engineer_review_reply(
            session_id=session_id,
            feedback_id=intake.feedback.feedback_id,
            applied="- 헤드라인 카피 교체\n- CTA 문구 강화",
            proposed=None,
            remaining="A/B 테스트",
        )

        self.assertIn("리뷰 회신", reply.message)
        self.assertIn("헤드라인 카피 교체", reply.message)
        self.assertEqual(len(reply.session.progress_notes), 1)

    def test_run_engineer_review_reply_requires_applied(self) -> None:
        session_id = self._intake_session()
        intake = discord_commands._run_engineer_review(
            session_id=session_id,
            summary="copy 부족",
            body=None,
            severity=None,
            categories="copy",
            source=None,
            file_paths=None,
            channel_id=None,
            thread_id=None,
            user_id=777,
            author_name="reviewer-1",
        )
        with self.assertRaises(ValueError):
            discord_commands._run_engineer_review_reply(
                session_id=session_id,
                feedback_id=intake.feedback.feedback_id,
                applied="",
                proposed=None,
                remaining=None,
            )
