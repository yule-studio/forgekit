"""Continuation prompt persistence regression suite.

The live MVP test surfaced a bug: when the user added a long research
request as a continuation to an existing session, the canonical prompt
stayed as the original confirmation phrase ("새 작업으로 진행") and
``session.extra`` had no record of the new request. Pin the new
contract here so every continuation turn lands the prompt + thread
context onto the session row.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import isolate_cache_for_test as _isolate_cache_for_test

from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_orchestrator.discord.bot import (
    _is_command_only_prompt,
    _record_engineering_continuation,
)


def _seed_session(prompt: str = "새 작업으로 진행") -> WorkflowSession:
    now = datetime.now(timezone.utc)
    session = WorkflowSession(
        session_id="abc123def456",
        prompt=prompt,
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now,
        updated_at=now,
        channel_id=1001,
        user_id=2002,
        thread_id=3003,
    )
    save_session(session)
    return session


class CommandOnlyPromptDetectionTests(unittest.TestCase):
    def test_typical_command_phrases_are_command_only(self) -> None:
        for text in (
            "새 작업으로 진행",
            "이대로 진행",
            "기존 세션으로 진행",
            "그대로 진행",
            "확정",
            "진행",
            "ok",
            "OK",
            "  새 작업으로 진행 ",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_command_only_prompt(text), text)

    def test_real_task_description_is_not_command_only(self) -> None:
        self.assertFalse(
            _is_command_only_prompt(
                "[Research] 하네스 엔지니어링 자동화 검토 — 운영-리서치에 자료 모아줘"
            )
        )
        self.assertFalse(
            _is_command_only_prompt(
                "결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘"
            )
        )

    def test_blank_or_whitespace_is_command_only(self) -> None:
        self.assertTrue(_is_command_only_prompt(""))
        self.assertTrue(_is_command_only_prompt("   "))


class RecordEngineeringContinuationTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)

    def test_continuation_prompt_lands_on_session_extra(self) -> None:
        session = _seed_session()
        long_prompt = (
            "[Research] 하네스 엔지니어링 자동화 검토 — 운영-리서치에 자료 모아줘"
        )

        updated = _record_engineering_continuation(
            session=session,
            continuation_prompt=long_prompt,
            resumed_thread_id=3003,
        )
        self.assertIsNotNone(updated)

        reloaded = load_session("abc123def456")
        self.assertIsNotNone(reloaded)
        extra = dict(reloaded.extra)
        self.assertEqual(extra["latest_continuation_prompt"], long_prompt)
        self.assertEqual(extra["resumed_thread_id"], 3003)
        history = extra.get("continuation_requests")
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["prompt"], long_prompt)
        self.assertEqual(history[0]["thread_id"], 3003)
        # Original session.prompt is preserved (we don't mutate it),
        # but the canonical_prompt_override is set because the prompt
        # was command-only.
        self.assertEqual(reloaded.prompt, "새 작업으로 진행")
        self.assertEqual(extra["canonical_prompt_override"], long_prompt)

    def test_continuation_does_not_override_real_canonical_prompt(self) -> None:
        # When the session was created with a real task description the
        # original prompt stays canonical and the continuation just
        # appends to history.
        session = _seed_session(
            prompt="결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘"
        )
        _record_engineering_continuation(
            session=session,
            continuation_prompt="추가로 retry/timeout 정책도 정리해줘",
            resumed_thread_id=3003,
        )
        reloaded = load_session("abc123def456")
        extra = dict(reloaded.extra)
        # Canonical override is intentionally absent — the original
        # prompt was already a real task description.
        self.assertNotIn("canonical_prompt_override", extra)
        self.assertEqual(
            extra["latest_continuation_prompt"],
            "추가로 retry/timeout 정책도 정리해줘",
        )

    def test_history_is_capped_at_twenty_entries(self) -> None:
        session = _seed_session()
        for i in range(25):
            _record_engineering_continuation(
                session=load_session("abc123def456") or session,
                continuation_prompt=f"continuation-{i}",
                resumed_thread_id=3003,
            )
        reloaded = load_session("abc123def456")
        history = reloaded.extra.get("continuation_requests")
        self.assertEqual(len(history), 20)
        # Latest 20 are kept; oldest are evicted.
        self.assertEqual(history[0]["prompt"], "continuation-5")
        self.assertEqual(history[-1]["prompt"], "continuation-24")
        self.assertEqual(reloaded.extra["latest_continuation_prompt"], "continuation-24")

    def test_blank_continuation_is_skipped(self) -> None:
        session = _seed_session()
        _record_engineering_continuation(
            session=session,
            continuation_prompt="   ",
            resumed_thread_id=3003,
        )
        reloaded = load_session("abc123def456")
        # Nothing was persisted — no extras added.
        self.assertNotIn("latest_continuation_prompt", reloaded.extra)
        self.assertNotIn("continuation_requests", reloaded.extra)


class ExtractSessionIdFromTextTests(unittest.TestCase):
    def test_pulls_id_from_korean_status_question(self) -> None:
        from yule_orchestrator.discord.bot import _extract_session_id_from_text

        cases = (
            "세션 a8d1707808ac 기준으로 운영 리서치 어디까지 됐어?",
            "세션 `a8d1707808ac` 정리 좀",
            "session a8d1707808ac 상태 알려줘",
            "기존 세션id=a8d1707808ac 어디까지 됐어",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    _extract_session_id_from_text(text), "a8d1707808ac"
                )

    def test_returns_none_when_no_session_keyword(self) -> None:
        from yule_orchestrator.discord.bot import _extract_session_id_from_text

        # A bare 12-hex token without "세션"/"session" must NOT match —
        # otherwise random hashes in URLs / commit shas would hijack.
        self.assertIsNone(
            _extract_session_id_from_text("commit a8d1707808ac 정리 좀")
        )
        self.assertIsNone(_extract_session_id_from_text(""))

    def test_returns_none_for_short_hex(self) -> None:
        from yule_orchestrator.discord.bot import _extract_session_id_from_text

        self.assertIsNone(_extract_session_id_from_text("세션 abcd 어디"))


class FindSessionWithResumedThreadTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)

    def test_finds_session_via_extra_resumed_thread_id(self) -> None:
        from yule_orchestrator.discord.bot import _find_session_with_resumed_thread

        session = _seed_session()
        # Manually persist resumed_thread_id (not on session.thread_id)
        # so the helper has to read session.extra.
        from dataclasses import replace
        from yule_orchestrator.agents.workflow_state import update_session

        updated = replace(
            session,
            extra={"resumed_thread_id": 9999},
        )
        update_session(updated, now=datetime.now(timezone.utc))

        match = _find_session_with_resumed_thread(9999)
        self.assertIsNotNone(match)
        self.assertEqual(match.session_id, "abc123def456")

    def test_returns_none_when_no_match(self) -> None:
        from yule_orchestrator.discord.bot import _find_session_with_resumed_thread

        _seed_session()
        self.assertIsNone(_find_session_with_resumed_thread(7777))


class StatusDiagnosticSurfacingTests(unittest.TestCase):
    def test_canonical_prompt_override_appears_in_diagnostic_body(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            format_status_diagnostic_response,
        )

        session = WorkflowSession(
            session_id="abc123def456",
            prompt="새 작업으로 진행",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            extra={
                "canonical_prompt_override": (
                    "[Research] 하네스 엔지니어링 자동화 검토"
                ),
                "latest_continuation_prompt": (
                    "[Research] 하네스 엔지니어링 자동화 검토"
                ),
                "resumed_thread_id": 1501466695251001434,
            },
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("canonical 작업 prompt", body)
        self.assertIn("하네스 엔지니어링", body)
        self.assertIn("이어붙인 thread id", body)
        # If canonical and latest are equal we don't print the
        # continuation line twice.
        self.assertNotIn("최근 continuation prompt", body)

    def test_continuation_prompt_appears_when_distinct_from_canonical(self) -> None:
        from yule_orchestrator.discord.engineering_conversation import (
            format_status_diagnostic_response,
        )

        session = WorkflowSession(
            session_id="abc123def456",
            prompt="결제 모듈 멱등성 추가",  # real prompt, no override
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            extra={
                "latest_continuation_prompt": "추가로 retry 정책도 정리해줘",
            },
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("최근 continuation prompt", body)
        self.assertIn("retry 정책", body)
        self.assertNotIn("canonical 작업 prompt", body)


if __name__ == "__main__":
    unittest.main()
