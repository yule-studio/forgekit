"""Refactor — engineering session resolver."""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.session_resolver import (
    RESOLVE_AMBIGUOUS,
    RESOLVE_NOT_FOUND,
    RESOLVE_OK,
    RESOLVE_UNAVAILABLE,
    extract_explicit_session_id,
    resolve_session_for_message,
)


class _FakeSession:
    def __init__(
        self,
        *,
        session_id: str,
        thread_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
        updated_at: Optional[datetime] = None,
        state: str = "in_progress",
        extra: Optional[dict] = None,
    ) -> None:
        self.session_id = session_id
        self.thread_id = thread_id
        self.channel_id = channel_id
        self.user_id = user_id
        self.updated_at = updated_at or datetime(2026, 5, 6)
        self.state = state
        self.extra = extra or {}


class _FakeChannel:
    def __init__(
        self,
        channel_id: int,
        *,
        parent_id: Optional[int] = None,
    ) -> None:
        self.id = channel_id
        self.parent_id = parent_id


class _FakeAuthor:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _FakeMessage:
    def __init__(self, channel: _FakeChannel, author: _FakeAuthor) -> None:
        self.channel = channel
        self.author = author


class ExtractExplicitSessionIdTests(unittest.TestCase):
    def test_korean(self) -> None:
        self.assertEqual(
            extract_explicit_session_id("세션 abc123def456 기준으로 상태"),
            "abc123def456",
        )

    def test_english_uppercase(self) -> None:
        self.assertEqual(
            extract_explicit_session_id("session ABC123DEF456 status"),
            "abc123def456",
        )

    def test_quoted(self) -> None:
        self.assertEqual(
            extract_explicit_session_id("세션 `abc123def456` 정리"),
            "abc123def456",
        )

    def test_no_keyword_returns_none(self) -> None:
        self.assertIsNone(extract_explicit_session_id("commit abc123def456"))


class ResolveSessionForMessageTests(unittest.TestCase):
    def _msg(
        self,
        *,
        channel_id: int = 1001,
        parent_id: Optional[int] = None,
        author_id: int = 4242,
    ) -> _FakeMessage:
        return _FakeMessage(
            _FakeChannel(channel_id, parent_id=parent_id),
            _FakeAuthor(author_id),
        )

    def test_explicit_id_wins(self) -> None:
        target = _FakeSession(session_id="abc123def456")
        result = resolve_session_for_message(
            message=self._msg(),
            text="세션 abc123def456 기준으로 저장해줘",
            list_sessions_fn=None,
            session_loader=lambda sid: target if sid == "abc123def456" else None,
        )
        self.assertEqual(result.status, RESOLVE_OK)
        self.assertIs(result.session, target)
        self.assertEqual(result.session_id, "abc123def456")

    def test_explicit_id_unknown_returns_not_found(self) -> None:
        result = resolve_session_for_message(
            message=self._msg(),
            text="세션 ffffffffffff 기준으로 저장해줘",
            list_sessions_fn=lambda **_: [],
            session_loader=lambda _sid: None,
        )
        self.assertEqual(result.status, RESOLVE_NOT_FOUND)
        self.assertIn("ffffffffffff", result.reason or "")

    def test_thread_anchor_match(self) -> None:
        target = _FakeSession(
            session_id="thread-bound", thread_id=909, channel_id=1001
        )
        other = _FakeSession(session_id="other", channel_id=1001)
        result = resolve_session_for_message(
            message=self._msg(channel_id=909, parent_id=1001),
            text="자료 더 모아줘",
            list_sessions_fn=lambda **_: [target, other],
        )
        self.assertEqual(result.status, RESOLVE_OK)
        self.assertEqual(result.session_id, "thread-bound")

    def test_forum_thread_anchor_match(self) -> None:
        target = _FakeSession(
            session_id="forum-bound",
            channel_id=1001,
            extra={"research_forum_thread_id": 7777},
        )
        other = _FakeSession(session_id="other", channel_id=1001)
        result = resolve_session_for_message(
            message=self._msg(channel_id=7777, parent_id=1001),
            text="조사 결과 정리해줘",
            list_sessions_fn=lambda **_: [target, other],
        )
        self.assertEqual(result.status, RESOLVE_OK)
        self.assertEqual(result.session_id, "forum-bound")

    def test_thread_anchor_ambiguous(self) -> None:
        a = _FakeSession(session_id="a", thread_id=909)
        b = _FakeSession(session_id="b", thread_id=909)
        result = resolve_session_for_message(
            message=self._msg(channel_id=909, parent_id=1001),
            text="자료",
            list_sessions_fn=lambda **_: [a, b],
        )
        self.assertEqual(result.status, RESOLVE_AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)

    def test_channel_user_fallback_single(self) -> None:
        target = _FakeSession(
            session_id="solo", channel_id=1001, user_id=4242
        )
        result = resolve_session_for_message(
            message=self._msg(channel_id=1001),
            text="hello",
            list_sessions_fn=lambda **_: [target],
            author_id=4242,
        )
        self.assertEqual(result.status, RESOLVE_OK)
        self.assertEqual(result.session_id, "solo")

    def test_channel_user_fallback_multiple_picks_latest(self) -> None:
        a = _FakeSession(
            session_id="a",
            channel_id=1001,
            user_id=4242,
            updated_at=datetime(2026, 5, 1),
        )
        b = _FakeSession(
            session_id="b",
            channel_id=1001,
            user_id=4242,
            updated_at=datetime(2026, 5, 5),
        )
        result = resolve_session_for_message(
            message=self._msg(channel_id=1001),
            text="hello",
            list_sessions_fn=lambda **_: [a, b],
            author_id=4242,
        )
        self.assertEqual(result.status, RESOLVE_OK)
        self.assertEqual(result.session_id, "b")  # latest
        self.assertEqual(len(result.candidates), 1)

    def test_no_open_sessions_returns_unavailable(self) -> None:
        result = resolve_session_for_message(
            message=self._msg(),
            text="status",
            list_sessions_fn=lambda **_: [],
        )
        self.assertEqual(result.status, RESOLVE_UNAVAILABLE)

    def test_terminal_state_sessions_filtered(self) -> None:
        # Completed / rejected sessions don't count toward open session
        # resolution. With everything filtered out the resolver
        # reports UNAVAILABLE — same shape as "no open sessions" so
        # callers handle both with one branch.
        completed = _FakeSession(
            session_id="done", channel_id=1001, user_id=4242, state="completed"
        )
        result = resolve_session_for_message(
            message=self._msg(channel_id=1001),
            text="status",
            list_sessions_fn=lambda **_: [completed],
            author_id=4242,
        )
        self.assertEqual(result.status, RESOLVE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
