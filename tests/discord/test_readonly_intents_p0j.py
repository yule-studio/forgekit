"""P0-J commit 7 — read-only intent hard-rule regression (#146).

Verifies that the 5 new intents (session_count / session_list /
blocked_reason / continue_existing_work / change_direction):

  * are classified correctly;
  * skip the auto_collect path entirely;
  * never emit ``ready_to_intake=True``;
  * never create a new research/intake.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.engineering_conversation import (
    BLOCKED_REASON_QUERY,
    CHANGE_DIRECTION,
    CONTINUE_EXISTING_WORK,
    READ_ONLY_INTENTS,
    SESSION_COUNT_QUERY,
    SESSION_LIST_QUERY,
    STATUS_DIAGNOSTIC,
    build_engineering_conversation_response,
    detect_engineering_intent,
)


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


class IntentClassificationTests(unittest.TestCase):
    def test_session_count_phrases(self) -> None:
        for text in (
            "지금 열려 있는 세션 작업들 몇 개 있어?",
            "열린 작업 몇 개야?",
            "오픈 세션 몇개 있어",
            "활성 세션 수 알려줘",
            "how many open sessions",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    detect_engineering_intent(text).intent_id,
                    SESSION_COUNT_QUERY,
                )

    def test_session_list_phrases(self) -> None:
        for text in (
            "오픈 세션 뭐뭐 있어?",
            "현재 진행 중인 세션 목록 보여줘",
            "세션 리스트 보여줘",
            "list open sessions",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    detect_engineering_intent(text).intent_id,
                    SESSION_LIST_QUERY,
                )

    def test_blocked_reason_phrases(self) -> None:
        for text in (
            "왜 멈췄어?",
            "뭐가 막혔어?",
            "왜 안 됐어?",
            "stuck",
            "왜 진행 안 되고 있어?",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    detect_engineering_intent(text).intent_id,
                    BLOCKED_REASON_QUERY,
                )

    def test_continue_existing_work_phrases(self) -> None:
        for text in (
            "이전 작업 이어서 해줘",
            "그 세션 계속 진행해",
            "기존 세션 이어가자",
            "resume session please",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    detect_engineering_intent(text).intent_id,
                    CONTINUE_EXISTING_WORK,
                )

    def test_change_direction_phrases(self) -> None:
        for text in (
            "자료 추가가 아니라 방향 수정이야",
            "검색 말고 로그인부터 먼저 해",
            "리서치 말고 구현으로 넘겨",
            "그쪽 말고 다른 쪽으로",
            "pivot 해줘",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    detect_engineering_intent(text).intent_id,
                    CHANGE_DIRECTION,
                )

    def test_read_only_intents_constant_complete(self) -> None:
        self.assertIn(STATUS_DIAGNOSTIC, READ_ONLY_INTENTS)
        self.assertIn(SESSION_COUNT_QUERY, READ_ONLY_INTENTS)
        self.assertIn(SESSION_LIST_QUERY, READ_ONLY_INTENTS)
        self.assertIn(BLOCKED_REASON_QUERY, READ_ONLY_INTENTS)
        self.assertIn(CONTINUE_EXISTING_WORK, READ_ONLY_INTENTS)
        self.assertIn(CHANGE_DIRECTION, READ_ONLY_INTENTS)


# ---------------------------------------------------------------------------
# Hard rule — no auto_collect / no intake for read-only intents
# ---------------------------------------------------------------------------


class NoAutoCollectHardRuleTests(unittest.TestCase):
    """Read-only intents must NEVER call _maybe_run_auto_collect."""

    def _assert_no_collect_called(self, text: str) -> None:
        with patch(
            "yule_orchestrator.discord.engineering_conversation.response_formatters._maybe_run_auto_collect"
        ) as mock_collect:
            response = build_engineering_conversation_response(text)
        # Hard rule: auto_collect must not have been called.
        self.assertEqual(
            mock_collect.call_count,
            0,
            f"auto_collect fired for {text!r} (intent={response.intent_id})",
        )
        # Read-only intent: never create new intake.
        self.assertFalse(response.ready_to_intake)
        # Read-only intent: is_status_query=True so router short-circuits.
        self.assertTrue(response.is_status_query)
        # Intent matches one of READ_ONLY_INTENTS.
        self.assertIn(response.intent_id, READ_ONLY_INTENTS)

    def test_session_count_query_no_auto_collect(self) -> None:
        self._assert_no_collect_called(
            "지금 열려 있는 세션 작업들 몇 개 있어?"
        )

    def test_session_list_query_no_auto_collect(self) -> None:
        self._assert_no_collect_called("오픈 세션 뭐뭐 있어?")

    def test_blocked_reason_query_no_auto_collect(self) -> None:
        self._assert_no_collect_called("왜 멈췄어?")

    def test_continue_existing_work_no_auto_collect(self) -> None:
        self._assert_no_collect_called("이전 작업 이어서 진행")

    def test_change_direction_no_auto_collect(self) -> None:
        self._assert_no_collect_called(
            "자료 추가가 아니라 방향 수정이야"
        )

    def test_status_diagnostic_no_auto_collect(self) -> None:
        # Sanity — existing STATUS_DIAGNOSTIC stays in the blocklist.
        self._assert_no_collect_called("지금 뭐 하는 중이야?")


# ---------------------------------------------------------------------------
# Genuine new work still uses auto_collect (no false negatives)
# ---------------------------------------------------------------------------


class GenuineNewWorkStillRunsCollectTests(unittest.TestCase):
    """An actual substantive new request must still go through intake/coding path."""

    def test_intake_candidate_still_runs_auto_collect(self) -> None:
        with patch(
            "yule_orchestrator.discord.engineering_conversation.response_formatters._maybe_run_auto_collect",
            return_value=None,
        ) as mock_collect:
            response = build_engineering_conversation_response(
                "회원가입 페이지 만들어줘 — Next.js + Postgres 로",
                user_links=("https://github.com/foo/bar/issues/1",),
            )
        # auto_collect was called (or attempted).
        self.assertGreaterEqual(mock_collect.call_count, 1)
        # NOT read-only.
        self.assertNotIn(response.intent_id, READ_ONLY_INTENTS)


# ---------------------------------------------------------------------------
# Response body sanity
# ---------------------------------------------------------------------------


class ResponseBodySanityTests(unittest.TestCase):
    def test_session_count_response_includes_count_phrase(self) -> None:
        response = build_engineering_conversation_response(
            "지금 열려 있는 세션 몇 개?"
        )
        self.assertIn("세션", response.content)

    def test_blocked_reason_with_no_session_yields_hint(self) -> None:
        response = build_engineering_conversation_response("왜 멈췄어?")
        self.assertIn("막힘", response.content)

    def test_continue_existing_with_no_session_yields_hint(self) -> None:
        response = build_engineering_conversation_response(
            "이전 작업 이어서 해"
        )
        # Hint about new intake not being created.
        self.assertIn("새 세션을 만들지 않고", response.content)

    def test_change_direction_acknowledges_without_new_intake(self) -> None:
        response = build_engineering_conversation_response(
            "리서치 말고 구현으로 넘겨"
        )
        self.assertIn("새 intake", response.content)


if __name__ == "__main__":
    unittest.main()
