"""decision.router — Phase 3 of #73."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.decision import (
    DecisionRequest,
    DecisionResult,
    MODE_CLARIFICATION_NEEDED,
    MODE_DISCUSSION,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_RESEARCH_ONLY,
    SOURCE_CLASSIFIER,
    SOURCE_FALLBACK,
    SOURCE_FAST_PATH,
    fake_classifier,
    route_decision,
)


class FastPathTests(unittest.TestCase):
    def test_research_only_keywords_route_to_research(self) -> None:
        for prompt in (
            "[Research] DevOps 학습 로드맵",
            "오늘은 자료 수집이 목표야 — 코드 수정 없이",
            "리서치만 해줘",
        ):
            with self.subTest(prompt=prompt):
                result = route_decision(DecisionRequest(prompt=prompt))
                self.assertEqual(result.mode, MODE_RESEARCH_ONLY)
                self.assertEqual(result.source, SOURCE_FAST_PATH)
                self.assertGreaterEqual(result.confidence, 0.85)

    def test_implementation_keywords_route_to_implementation(self) -> None:
        for prompt in (
            "이 버그 고쳐서 PR 올려줘",
            "users 401 회복 구현해줘",
            "리팩터링 좀 해줘",
            "Open a draft PR for the auth flow",
        ):
            with self.subTest(prompt=prompt):
                result = route_decision(DecisionRequest(prompt=prompt))
                self.assertEqual(result.mode, MODE_IMPLEMENTATION_CANDIDATE)
                self.assertEqual(result.source, SOURCE_FAST_PATH)

    def test_research_and_implementation_conflict_returns_clarification(self) -> None:
        result = route_decision(
            DecisionRequest(prompt="조사해줘 그리고 PR 올려줘")
        )
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertEqual(result.source, SOURCE_FAST_PATH)
        self.assertIn("research", result.reason.lower())

    def test_discussion_keywords_route_to_discussion(self) -> None:
        for prompt in ("어떻게 할까?", "토의 좀 해보자", "what should we do here?"):
            with self.subTest(prompt=prompt):
                result = route_decision(DecisionRequest(prompt=prompt))
                self.assertEqual(result.mode, MODE_DISCUSSION)


class FallbackTests(unittest.TestCase):
    def test_empty_prompt_returns_clarification_fallback(self) -> None:
        result = route_decision(DecisionRequest(prompt=""))
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertEqual(result.source, SOURCE_FALLBACK)
        self.assertEqual(result.confidence, 0.4)

    def test_unmatched_no_classifier_returns_clarification_fallback(self) -> None:
        result = route_decision(DecisionRequest(prompt="음 그냥 안녕하세요 정도?"))
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)
        self.assertEqual(result.source, SOURCE_FALLBACK)


class ClassifierFallbackTests(unittest.TestCase):
    def test_classifier_fires_when_fast_path_silent(self) -> None:
        captured = {}

        class _Recording:
            def classify(self, *, request, context_pack_id):
                captured["called"] = True
                captured["prompt"] = request.prompt
                return DecisionResult(
                    mode=MODE_DISCUSSION,
                    confidence=0.8,
                    reason="classifier said discussion",
                    source=SOURCE_CLASSIFIER,
                    matched_keywords=(),
                    context_pack_id=context_pack_id,
                    routed_at="",
                )

        result = route_decision(
            DecisionRequest(prompt="음 그냥 안녕하세요 정도?"),
            classifier=_Recording(),
            context_pack_id="ctx-1",
        )
        self.assertTrue(captured.get("called"))
        self.assertEqual(result.mode, MODE_DISCUSSION)
        self.assertEqual(result.source, SOURCE_CLASSIFIER)
        self.assertEqual(result.context_pack_id, "ctx-1")

    def test_classifier_skipped_when_fast_path_hits(self) -> None:
        called = {"yes": False}

        class _Loud:
            def classify(self, *, request, context_pack_id):
                called["yes"] = True
                return DecisionResult(
                    mode=MODE_DISCUSSION,
                    confidence=0.0,
                    reason="should not be reached",
                    source=SOURCE_CLASSIFIER,
                )

        route_decision(
            DecisionRequest(prompt="구현해줘"),
            classifier=_Loud(),
        )
        self.assertFalse(called["yes"])

    def test_fake_classifier_default_clarification(self) -> None:
        request = DecisionRequest(prompt="모호한 요청")
        result = fake_classifier(request=request, context_pack_id=None)
        self.assertEqual(result.mode, MODE_CLARIFICATION_NEEDED)


if __name__ == "__main__":
    unittest.main()
