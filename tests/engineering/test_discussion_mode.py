"""Tests for ``yule_orchestrator.agents.discussion.mode``."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    DiscussionMode,
    classify_discussion_mode,
)


class ClassifyDiscussionModeTestCase(unittest.TestCase):
    def test_explicit_research_only(self) -> None:
        for text in (
            "일단 조사만 해줘",
            "조사부터 해보자",
            "코드 수정 없이 자료만 모아줘",
            "리서치만 정리해줘",
            "정리까지만 해주면 돼",
            "research only please",
        ):
            with self.subTest(text=text):
                match = classify_discussion_mode(text)
                self.assertEqual(match.mode, DiscussionMode.RESEARCH_ONLY)
                self.assertEqual(match.source, "deterministic")

    def test_clarification_for_too_vague(self) -> None:
        for text in ("도와줘", "ㅎ", "ㅁㄴㅇ", "  ", "헤이"):
            with self.subTest(text=text):
                match = classify_discussion_mode(text)
                self.assertEqual(match.mode, DiscussionMode.CLARIFICATION_NEEDED)

    def test_discussion_for_design_questions(self) -> None:
        for text in (
            "이 구조 맞아?",
            "이건 devops 관점에서 어떻게 풀지?",
            "이 접근이 맞을까 — 토의 좀",
            "어떻게 생각해?",
            "리스크부터 정리하자",
            "어느 쪽이 좋을까",
        ):
            with self.subTest(text=text):
                match = classify_discussion_mode(text)
                self.assertEqual(match.mode, DiscussionMode.DISCUSSION)

    def test_implementation_for_explicit_write(self) -> None:
        for text in (
            "PR 올려줘",
            "구현 진행해주세요",
            "이 버그 고쳐줘",
            "Spring Security 컨피그 패치 작성",
            "기능 구현 부탁",
        ):
            with self.subTest(text=text):
                match = classify_discussion_mode(text)
                self.assertEqual(
                    match.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE,
                    f"text={text}, signals={match.signals}",
                )

    def test_review_signal_blocks_implementation(self) -> None:
        # 검토 신호 + 구현 동사가 같이 오면 implementation으로 가지 않고
        # discussion으로 받아야 한다.
        match = classify_discussion_mode(
            "이 구조 맞아? 그리고 PR 만들어 볼까?"
        )
        self.assertEqual(match.mode, DiscussionMode.DISCUSSION)

    def test_ambiguous_falls_back_to_discussion(self) -> None:
        # deterministic 신호가 없으면 LLM 부재 시 discussion fallback.
        match = classify_discussion_mode(
            "users 테이블에 email_verified 칼럼 관련해서 한번 봐주세요"
        )
        self.assertEqual(match.mode, DiscussionMode.DISCUSSION)
        self.assertEqual(match.source, "fallback")

    def test_llm_classifier_used_only_when_deterministic_undecided(self) -> None:
        called: list[str] = []

        def fake_llm(*, message_text, normalized, context_pack):
            called.append(message_text)
            return DiscussionMode.RESEARCH_ONLY

        # 명확한 implementation은 deterministic이라 LLM 호출 없음.
        match = classify_discussion_mode("PR 올려줘", llm_classifier=fake_llm)
        self.assertEqual(match.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE)
        self.assertEqual(called, [])

        # 모호한 요청만 LLM seam을 친다.
        match = classify_discussion_mode(
            "users 테이블에 email_verified 추가에 대해서",
            llm_classifier=fake_llm,
        )
        self.assertEqual(match.mode, DiscussionMode.RESEARCH_ONLY)
        self.assertEqual(match.source, "llm")
        self.assertEqual(len(called), 1)

    def test_llm_classifier_failure_falls_back(self) -> None:
        def crashing_llm(**_kwargs):
            raise RuntimeError("offline")

        match = classify_discussion_mode(
            "users 테이블 마이그 관련 어떻게 처리할지",
            llm_classifier=crashing_llm,
        )
        self.assertEqual(match.mode, DiscussionMode.DISCUSSION)
        self.assertEqual(match.source, "fallback")

    def test_llm_classifier_invalid_value_falls_back(self) -> None:
        match = classify_discussion_mode(
            "users 마이그 관련 묻고 싶음",
            llm_classifier=lambda **_: "garbage",
        )
        self.assertEqual(match.mode, DiscussionMode.DISCUSSION)
        self.assertEqual(match.source, "fallback")

    def test_llm_classifier_dict_with_rationale(self) -> None:
        match = classify_discussion_mode(
            "users 변경 검토해줄래",
            llm_classifier=lambda **_: {
                "mode": "implementation_candidate",
                "rationale": "explicit verb 발견 (LLM)",
                "signals": ["llm_keyword:변경"],
            },
        )
        self.assertEqual(match.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE)
        self.assertEqual(match.source, "llm")
        self.assertEqual(match.rationale, "explicit verb 발견 (LLM)")
        self.assertEqual(match.signals, ("llm_keyword:변경",))


if __name__ == "__main__":
    unittest.main()
