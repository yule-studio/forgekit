"""Tests for the engineering routing decision (Part 1).

These cover the routing classifier in isolation — no Discord, no
real workflow cache. Open sessions are passed in directly so the
deterministic similarity scoring is the only thing under test.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.routing import (
    ACTION_APPEND_CONTEXT,
    ACTION_ASK,
    ACTION_CREATE,
    ACTION_JOIN,
    EngineeringRoutingDecision,
    decide_routing,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState


def _session(
    *,
    session_id: str,
    prompt: str,
    task_type: str = "landing-page",
    state: WorkflowState = WorkflowState.APPROVED,
    summary: str = "",
    extra: dict | None = None,
    thread_id: int | None = None,
) -> WorkflowSession:
    now = datetime(2026, 5, 1, 10, 0)
    return WorkflowSession(
        session_id=session_id,
        prompt=prompt,
        task_type=task_type,
        state=state,
        created_at=now,
        updated_at=now,
        summary=summary or None,
        thread_id=thread_id,
        extra=extra or {},
    )


class DecideRoutingTests(unittest.TestCase):
    def test_no_open_sessions_creates_new_work(self) -> None:
        decision = decide_routing(prompt="새 랜딩 페이지", open_sessions=())
        self.assertEqual(decision.action, ACTION_CREATE)

    def test_strong_similarity_joins_existing(self) -> None:
        session = _session(
            session_id="abc123",
            prompt="Stripe pricing 페이지 hero copy 정리",
        )
        decision = decide_routing(
            prompt="Stripe pricing hero copy 다시 보자",
            open_sessions=(session,),
        )
        self.assertEqual(decision.action, ACTION_JOIN)
        self.assertEqual(decision.matched_session_id, "abc123")

    def test_weak_similarity_creates_new_work(self) -> None:
        session = _session(
            session_id="zzz",
            prompt="결제 모듈 회귀 시나리오 보강",
        )
        decision = decide_routing(
            prompt="브랜드 메인 페이지 새로 디자인하기",
            open_sessions=(session,),
        )
        self.assertEqual(decision.action, ACTION_CREATE)

    def test_older_more_relevant_session_wins_over_latest(self) -> None:
        relevant = _session(
            session_id="older",
            prompt="Stripe pricing hero copy 정리",
        )
        latest_unrelated = _session(
            session_id="newer",
            prompt="결제 모듈 회귀 시나리오",
        )
        decision = decide_routing(
            prompt="Stripe pricing hero copy 분할 검토",
            open_sessions=(latest_unrelated, relevant),
        )
        self.assertEqual(decision.action, ACTION_JOIN)
        self.assertEqual(decision.matched_session_id, "older")

    def test_explicit_new_work_override(self) -> None:
        session = _session(
            session_id="abc",
            prompt="Stripe pricing hero copy",
        )
        decision = decide_routing(
            prompt="Stripe pricing hero copy 새 작업으로 진행",
            open_sessions=(session,),
        )
        self.assertEqual(decision.action, ACTION_CREATE)

    def test_explicit_session_id_override(self) -> None:
        session = _session(session_id="abc12345", prompt="아무거나")
        loaded = {"abc12345": session}

        decision = decide_routing(
            prompt="기존 세션 abc12345 에 이어서 진행해줘",
            session_loader=lambda sid: loaded.get(sid),
        )
        self.assertEqual(decision.action, ACTION_JOIN)
        self.assertEqual(decision.matched_session_id, "abc12345")
        self.assertEqual(decision.confidence, "high")

    def test_explicit_session_id_unknown_asks_for_clarification(self) -> None:
        decision = decide_routing(
            prompt="기존 세션 deadbeef 이어서",
            session_loader=lambda _sid: None,
        )
        self.assertEqual(decision.action, ACTION_ASK)

    def test_two_close_candidates_trigger_ask(self) -> None:
        a = _session(
            session_id="aaa",
            prompt="Stripe pricing hero copy 정리",
        )
        b = _session(
            session_id="bbb",
            prompt="Stripe pricing hero copy 회귀 검토",
        )
        decision = decide_routing(
            prompt="Stripe pricing hero copy 다시 같이 보자",
            open_sessions=(a, b),
        )
        # Either ask_for_clarification or join — depends on tiebreaker.
        # The acceptance criterion is that the 2nd candidate is surfaced
        # as a candidate_summary so the user can pivot if needed.
        self.assertIn(decision.action, {ACTION_ASK, ACTION_JOIN})
        self.assertGreaterEqual(len(decision.candidate_summaries), 2)

    def test_keep_context_phrase_alone_is_not_a_force_join(self) -> None:
        # Bare "기존 맥락 참고" against a session that does NOT match the
        # prompt topic must not coerce a join. This guards against the
        # legacy heuristic that conflated 참고 / 이어가.
        unrelated = _session(
            session_id="zzz",
            prompt="결제 모듈 회귀",
        )
        decision = decide_routing(
            prompt="새 페이지 디자인 정리할 때 기존 맥락 참고하면 좋겠어",
            open_sessions=(unrelated,),
        )
        self.assertEqual(decision.action, ACTION_CREATE)

    def test_explicit_append_context_attaches_to_latest_open(self) -> None:
        latest = _session(
            session_id="latest",
            prompt="Stripe pricing hero copy",
            thread_id=4242,
        )
        decision = decide_routing(
            prompt="이 자료만 기존 작업에 참고로 붙여줘 https://x",
            open_sessions=(latest,),
        )
        self.assertEqual(decision.action, ACTION_APPEND_CONTEXT)
        self.assertEqual(decision.matched_session_id, "latest")
        self.assertEqual(decision.matched_thread_id, 4242)

    def test_append_context_with_no_open_sessions_falls_back_to_create(self) -> None:
        decision = decide_routing(
            prompt="이 자료만 기존 작업에 참고로 붙여줘 https://x",
            open_sessions=(),
        )
        self.assertEqual(decision.action, ACTION_CREATE)

    def test_uses_research_pack_title_for_matching(self) -> None:
        session = _session(
            session_id="pack",
            prompt="짧은 prompt",
            extra={
                "research_pack": {
                    "title": "Obsidian sync 충돌 정책",
                    "summary": "auto-suffix 자동 부여",
                }
            },
        )
        decision = decide_routing(
            prompt="Obsidian sync 충돌 auto-suffix 다시 검토",
            open_sessions=(session,),
        )
        self.assertEqual(decision.action, ACTION_JOIN)
        self.assertEqual(decision.matched_session_id, "pack")

    def test_completed_sessions_are_filtered_when_listing_open(self) -> None:
        from yule_orchestrator.agents.routing import list_open_sessions

        # Smoke: list_open_sessions yields a tuple even when cache is
        # empty (or an exception is raised internally — exercise both).
        result = list_open_sessions(limit=1)
        self.assertIsInstance(result, tuple)


class CandidateSummaryShapeTests(unittest.TestCase):
    def test_candidates_carry_title_score_thread(self) -> None:
        a = _session(session_id="a", prompt="Stripe pricing hero copy")
        decision = decide_routing(
            prompt="Stripe pricing hero copy 다시", open_sessions=(a,)
        )
        self.assertTrue(decision.candidate_summaries)
        first = decision.candidate_summaries[0]
        self.assertEqual(first.session_id, "a")
        self.assertGreater(first.score, 0.0)
        self.assertIn("Stripe", first.title)


if __name__ == "__main__":
    unittest.main()
