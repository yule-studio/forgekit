"""P0-F commit 4 — forum thread conversational follow-up adapter.

Covers branch 3 of forum_message_adapter (newly added) + the
underlying handle_forum_followup helper:

  1. Status question ("지금 뭐하고 있어?") → conversation helper
     returns a status reply, sender receives it, no new intake.
  2. Append-context directive ("이 링크도 참고해") → light parser
     persists a note into session.extra['forum_followup_notes'].
  3. Correction directive ("RAG 말고 CAG 기준으로 봐줘") → same.
  4. Summarize directive ("지금까지 합의만 요약해줘") → same.
  5. helper suggests ready_to_intake → dropped (forum is not
     an intake surface).
  6. No session anchored to the thread → handled=False (fall through).
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.discord.forum.conversation_adapter import (
    ForumFollowupResult,
    RESPONSE_NOTE_RECORDED,
    SKIPPED_HELPER_SUGGESTED_INTAKE,
    SKIPPED_NO_SESSION_ANCHOR,
    detect_followup_directive,
    handle_forum_followup,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _forum_message(content: str):
    channel = SimpleNamespace(id=50001, parent_id=50000, name="thread")
    author = SimpleNamespace(id=7, name="masterway", global_name="masterway")
    return SimpleNamespace(
        id=60001,
        channel=channel,
        author=author,
        content=content,
    )


def _open_session(extra_overrides=None):
    extra = {"research_forum_thread_id": 50001}
    if extra_overrides:
        extra.update(extra_overrides)
    return SimpleNamespace(
        session_id="sess-followup-1",
        prompt="k8s 운영 자료 정리",
        extra=extra,
        thread_id=None,
        role_sequence=("tech-lead",),
        updated_at=datetime.now(tz=timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Directive detection
# ---------------------------------------------------------------------------


class DetectFollowupDirectiveTests(unittest.TestCase):
    def test_correction_with_malgo(self) -> None:
        self.assertEqual(
            detect_followup_directive("RAG 말고 CAG 기준으로 봐줘"),
            "correction",
        )

    def test_correction_with_beggo(self) -> None:
        self.assertEqual(
            detect_followup_directive("backend 빼고 ai만 더 봐줘"),
            "correction",
        )

    def test_append_context_link(self) -> None:
        self.assertEqual(
            detect_followup_directive("이 링크도 참고해 줘"),
            "append",
        )

    def test_summarize(self) -> None:
        self.assertEqual(
            detect_followup_directive("지금까지 합의만 짧게 말해줘"),
            "summarize",
        )

    def test_no_directive(self) -> None:
        self.assertIsNone(detect_followup_directive("그냥 평범한 메시지"))


# ---------------------------------------------------------------------------
# Adapter behavior
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.sent: List[str] = []
        self.updated_sessions: List = []

    async def send_chunks(self, channel, content, *args, **kwargs):
        self.sent.append(content)

    def session_updater(self, session, *, now=None):
        self.updated_sessions.append((session, now))


class HandleForumFollowupTests(unittest.TestCase):
    def test_status_question_dispatches_to_helper(self) -> None:
        session = _open_session()
        recorder = _Recorder()

        def fake_helper(**kwargs):
            return SimpleNamespace(
                content="현재 진행 중인 작업: …",
                intent_id="status_diagnostic",
                ready_to_intake=False,
                is_status_query=True,
            )

        result = _run(
            handle_forum_followup(
                message=_forum_message("지금 뭐하고 있어?"),
                text="지금 뭐하고 있어?",
                session=session,
                conversation_fn=fake_helper,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )

        self.assertTrue(result.handled)
        self.assertIn("진행 중", result.response_sent or "")
        self.assertEqual(result.intent_id, "status_diagnostic")
        # Status query is not a directive → no note persisted.
        self.assertEqual(recorder.updated_sessions, [])

    def test_append_directive_persists_note(self) -> None:
        session = _open_session()
        recorder = _Recorder()

        result = _run(
            handle_forum_followup(
                message=_forum_message("이 링크도 참고해줘"),
                text="이 링크도 참고해줘 https://example.com/cache",
                session=session,
                conversation_fn=lambda **_: None,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.followup_note_recorded, "append")
        self.assertEqual(result.response_sent, RESPONSE_NOTE_RECORDED)
        # Note actually landed in session.extra.
        notes = session.extra["forum_followup_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["directive"], "append")
        self.assertIn("example.com/cache", notes[0]["text"])

    def test_correction_directive_persists_note(self) -> None:
        session = _open_session()
        recorder = _Recorder()

        result = _run(
            handle_forum_followup(
                message=_forum_message("RAG 말고 CAG 기준으로 봐줘"),
                text="RAG 말고 CAG 기준으로 봐줘",
                session=session,
                conversation_fn=lambda **_: None,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.followup_note_recorded, "correction")
        notes = session.extra["forum_followup_notes"]
        self.assertEqual(notes[0]["directive"], "correction")

    def test_helper_intake_suggestion_dropped(self) -> None:
        session = _open_session()
        recorder = _Recorder()

        def fake_helper(**kwargs):
            return SimpleNamespace(
                content="좋습니다. 이대로 등록할게요.",
                intent_id="confirm_intake",
                ready_to_intake=True,
                is_status_query=False,
            )

        result = _run(
            handle_forum_followup(
                message=_forum_message("이대로 진행해"),
                text="이대로 진행해",
                session=session,
                conversation_fn=fake_helper,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )

        # Forum is not an intake surface → drop.
        self.assertFalse(result.handled)
        self.assertEqual(result.skipped_reason, SKIPPED_HELPER_SUGGESTED_INTAKE)
        # Nothing got sent.
        self.assertEqual(recorder.sent, [])

    def test_no_session_falls_through(self) -> None:
        recorder = _Recorder()
        result = _run(
            handle_forum_followup(
                message=_forum_message("아무거나"),
                text="아무거나",
                session=None,
                conversation_fn=lambda **_: None,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )
        self.assertFalse(result.handled)
        self.assertEqual(result.skipped_reason, SKIPPED_NO_SESSION_ANCHOR)
        self.assertEqual(recorder.sent, [])

    def test_helper_returns_none_body_falls_through(self) -> None:
        session = _open_session()
        recorder = _Recorder()

        def fake_helper(**kwargs):
            return SimpleNamespace(
                content="   ",
                intent_id="empty",
                ready_to_intake=False,
                is_status_query=False,
            )

        result = _run(
            handle_forum_followup(
                message=_forum_message("의미없음"),
                text="의미없음",
                session=session,
                conversation_fn=fake_helper,
                session_updater=recorder.session_updater,
                send_chunks=recorder.send_chunks,
            )
        )

        self.assertFalse(result.handled)


if __name__ == "__main__":
    unittest.main()
