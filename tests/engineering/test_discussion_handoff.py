"""Tests for ``yule_orchestrator.agents.discussion.handoff``."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    ContextPack,
    DiscussionMode,
    DiscussionSynthesis,
    build_implementation_handoff,
)


def _impl_synth(*, ready: bool = True) -> DiscussionSynthesis:
    return DiscussionSynthesis(
        mode=DiscussionMode.IMPLEMENTATION_CANDIDATE,
        rationale="impl",
        response_text="hello",
        implementation_ready=ready,
    )


def _other_synth(mode: DiscussionMode) -> DiscussionSynthesis:
    return DiscussionSynthesis(
        mode=mode,
        rationale="x",
        response_text="x",
    )


class BuildImplementationHandoffTestCase(unittest.TestCase):
    def test_proposal_for_clear_implementation(self) -> None:
        pack = ContextPack(
            current_message="Spring Security 인증 마이그 패치 작성, API 흐름도 조정해줘",
            session_id="abc12345",
        )
        handoff = build_implementation_handoff(synthesis=_impl_synth(), pack=pack)
        self.assertIsNotNone(handoff.proposal)
        self.assertEqual(handoff.proposal.executor_role, "backend-engineer")
        self.assertIn("권한 제안을 만들었습니다", handoff.follow_up_text)
        self.assertIsNone(handoff.blocker)

    def test_skips_when_not_implementation_mode(self) -> None:
        for mode in (
            DiscussionMode.DISCUSSION,
            DiscussionMode.RESEARCH_ONLY,
            DiscussionMode.CLARIFICATION_NEEDED,
        ):
            with self.subTest(mode=mode):
                handoff = build_implementation_handoff(
                    synthesis=_other_synth(mode),
                    pack=ContextPack(current_message="x"),
                )
                self.assertIsNone(handoff.proposal)
                self.assertIsNotNone(handoff.blocker)

    def test_skips_when_implementation_ready_false(self) -> None:
        handoff = build_implementation_handoff(
            synthesis=_impl_synth(ready=False),
            pack=ContextPack(current_message="x"),
        )
        self.assertIsNone(handoff.proposal)
        self.assertIsNotNone(handoff.blocker)

    def test_blocks_when_user_request_empty(self) -> None:
        pack = ContextPack(current_message="   ")
        handoff = build_implementation_handoff(synthesis=_impl_synth(), pack=pack)
        self.assertIsNone(handoff.proposal)
        assert handoff.blocker is not None
        self.assertEqual(handoff.blocker.reason, "user_request가 비어 있음")

    def test_falls_back_to_thread_summary(self) -> None:
        pack = ContextPack(
            current_message="",
            thread_summary="auth 마이그 PR 만들어 달라는 누적 thread",
        )
        handoff = build_implementation_handoff(synthesis=_impl_synth(), pack=pack)
        # auth/마이그 키워드 → backend-engineer
        self.assertIsNotNone(handoff.proposal)

    def test_research_only_recommendation_becomes_blocker(self) -> None:
        # 본문이 "조사만" 신호를 강하게 가지면 recommend_authorization은
        # research-only로 떨어진다. 토의 분류가 implementation이라고 봤어
        # 도, handoff는 충돌 신호로 처리해 사용자에게 다시 묻게 한다.
        pack = ContextPack(current_message="조사만 해줘 — auth 흐름 정리")
        handoff = build_implementation_handoff(synthesis=_impl_synth(), pack=pack)
        self.assertIsNone(handoff.proposal)
        assert handoff.blocker is not None
        self.assertIn("research-only", handoff.blocker.reason)


if __name__ == "__main__":
    unittest.main()
