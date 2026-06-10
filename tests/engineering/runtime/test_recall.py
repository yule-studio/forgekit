"""Phase 3 — runtime Recall stage: session candidate lookup.

Recall translates "어제/방금/그 작업" / "헤르메스 작업" / arbitrary
back-references into actual workflow session candidates. The tests
here use fake session objects (no DB / no IO) so the rules stay
pinned exactly: thread anchor wins, named project beats vague
recency, ambiguous candidates do NOT auto-attach, no open sessions
yields a safe empty result.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_runtime import (
    INTENT_APPEND_CONTEXT,
    INTENT_CONTINUE_EXISTING_WORK,
    INTENT_EXECUTE_EXISTING_STEP,
    INTENT_NEW_WORK_REQUEST,
    INTENT_STATUS_QUESTION,
    INTENT_SUMMARIZE_PREVIOUS_WORK,
    RuntimeInput,
    RuntimeIntent,
    RuntimeObservation,
)
from yule_agent_runtime.recall import (
    AMBIGUITY_MARGIN,
    SCORE_HIGH,
    make_recall_fn,
)


@dataclass
class FakeSession:
    session_id: str
    prompt: str = ""
    task_type: str = "unknown"
    state: str = "in_progress"
    summary: Optional[str] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def _now(offset_minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


def _obs(text: str) -> RuntimeObservation:
    return RuntimeObservation(
        role_id="gateway",
        message_text=text,
        normalized_text=" ".join(text.lower().split()),
    )


def _input(text: str, *, channel_id=None, thread_id=None) -> RuntimeInput:
    return RuntimeInput(
        role_id="gateway",
        message_text=text,
        channel_id=channel_id,
        thread_id=thread_id,
    )


class NoOpenSessionsTests(unittest.TestCase):
    def test_returns_safe_empty_result_when_no_sessions(self) -> None:
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: [])
        result = recall(
            _obs("어제 작업 이어서 요약해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("어제 작업 이어서 요약해줘"),
        )
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.confidence, "low")
        self.assertIsNone(result.matched_session_id)


class NamedProjectTokenMatchTests(unittest.TestCase):
    def test_hermes_matches_session_with_hermes_in_pack_title(self) -> None:
        sessions = [
            FakeSession(
                session_id="ab12",
                prompt="결제 모듈 멱등성 검토",
                updated_at=_now(-90),
            ),
            FakeSession(
                session_id="cd34",
                prompt="헤르메스 RAG 구조 설계 작업",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "헤르메스 학습 루프"}},
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("헤르메스 작업 이어서 가자"),
            RuntimeIntent(intent_id=INTENT_CONTINUE_EXISTING_WORK),
            _input("헤르메스 작업 이어서 가자"),
        )
        self.assertEqual(result.matched_session_id, "cd34")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.candidates[0].session_id, "cd34")

    def test_named_project_does_not_match_unrelated_sessions(self) -> None:
        sessions = [
            FakeSession(
                session_id="x",
                prompt="결제 모듈 멱등성 검토",
                updated_at=_now(-30),
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("헤르메스 작업 이어서 가자"),
            RuntimeIntent(intent_id=INTENT_CONTINUE_EXISTING_WORK),
            _input("헤르메스 작업 이어서 가자"),
        )
        self.assertIsNone(result.matched_session_id)


class RecencyFallbackTests(unittest.TestCase):
    def test_yesterday_falls_back_to_latest_open_session(self) -> None:
        sessions = [
            FakeSession(
                session_id="old",
                prompt="결제 모듈 멱등성 검토",
                updated_at=_now(-1440),
            ),
            FakeSession(
                session_id="new",
                prompt="onboarding flow 검토",
                updated_at=_now(-30),
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("어제 작업 이어서 요약해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("어제 작업 이어서 요약해줘"),
        )
        self.assertEqual(result.matched_session_id, "new")
        self.assertEqual(result.confidence, "medium")

    def test_no_recency_cue_no_token_match_yields_no_attachment(self) -> None:
        sessions = [
            FakeSession(
                session_id="x",
                prompt="결제 모듈 멱등성 검토",
                updated_at=_now(-30),
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        # No "어제/방금" cue and no token overlap with the prompt.
        result = recall(
            _obs("Obsidian에 정리해줘"),
            RuntimeIntent(intent_id=INTENT_EXECUTE_EXISTING_STEP),
            _input("Obsidian에 정리해줘"),
        )
        self.assertIsNone(result.matched_session_id)
        self.assertEqual(result.confidence, "low")


class AmbiguityTests(unittest.TestCase):
    def test_two_similar_sessions_do_not_auto_attach(self) -> None:
        sessions = [
            FakeSession(
                session_id="a",
                prompt="hermes 학습 루프 구조 설계",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
            FakeSession(
                session_id="b",
                prompt="hermes 학습 루프 구조 검증",
                updated_at=_now(-60),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("hermes 학습 루프 구조 어떻게 됐어?"),
            RuntimeIntent(intent_id=INTENT_STATUS_QUESTION),
            _input("hermes 학습 루프 구조 어떻게 됐어?"),
        )
        # Either both candidates surface and we don't pick (margin too
        # small) or one wins clearly — but never silently auto-create.
        if result.matched_session_id is not None:
            top = result.candidates[0]
            runner_up = result.candidates[1]
            self.assertGreaterEqual(top.score - runner_up.score, AMBIGUITY_MARGIN)
        self.assertGreaterEqual(len(result.candidates), 2)


class ThreadAnchorTests(unittest.TestCase):
    def test_thread_id_anchor_wins_over_token_score(self) -> None:
        sessions = [
            FakeSession(
                session_id="thread-bound",
                prompt="다른 주제 자료",
                thread_id=4242,
                updated_at=_now(-5),
            ),
            FakeSession(
                session_id="token-match",
                prompt="hermes 학습 루프",
                updated_at=_now(-1),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("hermes 학습 루프 어디까지 됐어?"),
            RuntimeIntent(intent_id=INTENT_STATUS_QUESTION),
            _input("hermes 학습 루프 어디까지 됐어?", thread_id=4242),
        )
        self.assertEqual(result.matched_session_id, "thread-bound")
        self.assertEqual(result.confidence, "high")


class NewWorkRequestTests(unittest.TestCase):
    def test_new_work_does_not_auto_attach_even_with_anchor(self) -> None:
        sessions = [
            FakeSession(
                session_id="thread-bound",
                prompt="다른 작업",
                thread_id=4242,
                updated_at=_now(-5),
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("새 작업으로 진행"),
            RuntimeIntent(intent_id=INTENT_NEW_WORK_REQUEST),
            _input("새 작업으로 진행", thread_id=4242),
        )
        self.assertIsNone(result.matched_session_id)
        # Anchor session is still surfaced so Decide can warn.
        self.assertEqual(result.candidates[0].session_id, "thread-bound")


class CompletedSessionsAreSkippedTests(unittest.TestCase):
    def test_completed_session_does_not_match(self) -> None:
        sessions = [
            FakeSession(
                session_id="done",
                prompt="hermes 학습 루프",
                state="completed",
                updated_at=_now(-30),
                extra={"research_pack": {"title": "hermes"}},
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("헤르메스 작업 정리해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("헤르메스 작업 정리해줘"),
        )
        self.assertIsNone(result.matched_session_id)


class CandidateDataShapeTests(unittest.TestCase):
    def test_candidate_carries_pack_and_synthesis_flags(self) -> None:
        sessions = [
            FakeSession(
                session_id="abc",
                prompt="hermes 학습 루프 구조",
                state="in_progress",
                thread_id=99,
                updated_at=_now(-5),
                extra={
                    "research_pack": {"title": "hermes 학습 루프"},
                    "research_synthesis": {"consensus": "안정화된 안"},
                    "research_forum_thread_id": 555,
                },
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("hermes 학습 루프 구조 정리해줘"),
            RuntimeIntent(intent_id=INTENT_SUMMARIZE_PREVIOUS_WORK),
            _input("hermes 학습 루프 구조 정리해줘"),
        )
        self.assertEqual(result.matched_session_id, "abc")
        self.assertEqual(result.matched_forum_thread_id, 555)
        self.assertEqual(result.matched_thread_id, 99)
        cand = result.candidates[0]
        self.assertTrue(cand.has_research_pack)
        self.assertTrue(cand.has_synthesis)
        self.assertEqual(cand.title, "hermes 학습 루프")


class AppendContextRecallTests(unittest.TestCase):
    def test_append_context_uses_token_match_against_existing(self) -> None:
        sessions = [
            FakeSession(
                session_id="abc",
                prompt="hermes 학습 루프 구조 설계",
                updated_at=_now(-5),
                extra={"research_pack": {"title": "hermes 학습 루프"}},
            ),
        ]
        recall = make_recall_fn(list_sessions_fn=lambda **_kw: sessions)
        result = recall(
            _obs("이 자료만 hermes 학습 루프에 참고로 붙여줘"),
            RuntimeIntent(intent_id=INTENT_APPEND_CONTEXT),
            _input("이 자료만 hermes 학습 루프에 참고로 붙여줘"),
        )
        self.assertEqual(result.matched_session_id, "abc")


if __name__ == "__main__":
    unittest.main()
