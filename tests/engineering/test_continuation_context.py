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


if __name__ == "__main__":
    unittest.main()
