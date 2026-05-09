"""Tests for ``yule_orchestrator.agents.discussion.synthesizer``."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    ContextPack,
    DiscussionMode,
    DiscussionModeMatch,
    DiscussionSynthesis,
    GithubIssueRef,
    GithubPRRef,
    ObsidianNoteRef,
    synthesize_discussion,
)


def _classification(mode: DiscussionMode, *, signals=()) -> DiscussionModeMatch:
    return DiscussionModeMatch(
        mode=mode,
        rationale="test classification",
        signals=tuple(signals),
        source="deterministic",
    )


class SynthesizeDiscussionTestCase(unittest.TestCase):
    def test_clarification_lists_questions(self) -> None:
        pack = ContextPack(current_message="ㅎ")
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.CLARIFICATION_NEEDED),
        )
        self.assertEqual(synth.mode, DiscussionMode.CLARIFICATION_NEEDED)
        self.assertGreaterEqual(len(synth.open_questions), 2)
        self.assertIn("어디부터 봐야", synth.response_text)

    def test_research_lists_followups(self) -> None:
        pack = ContextPack(
            current_message="auth 마이그레이션 자료 정리",
            suggested_task_type="backend-feature",
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.RESEARCH_ONLY),
        )
        self.assertEqual(synth.mode, DiscussionMode.RESEARCH_ONLY)
        self.assertTrue(any("research profile" in f for f in synth.research_followups))
        self.assertIn("운영-리서치", synth.response_text)
        # research profile 미주입 시 blocker 라인 추가.
        self.assertTrue(
            any("research profile 미주입" in b for b in synth.blockers),
            f"blockers={synth.blockers}",
        )

    def test_discussion_lists_role_perspectives(self) -> None:
        pack = ContextPack(
            current_message="이 구조 맞아? devops 관점에서 어떻게 풀지",
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.DISCUSSION),
        )
        self.assertEqual(synth.mode, DiscussionMode.DISCUSSION)
        self.assertIn("devops-engineer", synth.role_perspectives)
        self.assertIn("**devops-engineer**", synth.response_text)
        self.assertIn("권한 제안", synth.response_text)

    def test_discussion_includes_related_links(self) -> None:
        pack = ContextPack(
            current_message="hero 섹션 재작업",
            related_issues=(
                GithubIssueRef(number=42, title="hero hero", state="open"),
            ),
            related_prs=(
                GithubPRRef(number=99, title="hero attempt 1", state="closed"),
            ),
            relevant_notes=(
                ObsidianNoteRef(title="hero 회의록", path="notes/hero.md"),
            ),
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.DISCUSSION),
        )
        self.assertIn("issue #42", synth.response_text)
        self.assertIn("PR #99", synth.response_text)
        self.assertIn("hero 회의록", synth.response_text)

    def test_implementation_marks_ready_and_includes_executor_hint(self) -> None:
        pack = ContextPack(
            current_message="API auth 흐름 패치 작성, schema 마이그도 같이",
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.IMPLEMENTATION_CANDIDATE),
        )
        self.assertEqual(synth.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE)
        self.assertTrue(synth.implementation_ready)
        self.assertEqual(synth.suggested_handoff_role, "backend-engineer")
        self.assertIn("권한 제안", synth.response_text)

    def test_llm_synthesizer_failure_falls_back(self) -> None:
        pack = ContextPack(current_message="x")

        def crash(**_):
            raise RuntimeError("offline")

        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.DISCUSSION),
            llm_synthesizer=crash,
        )
        self.assertEqual(synth.mode, DiscussionMode.DISCUSSION)

    def test_llm_synthesizer_can_replace_response(self) -> None:
        pack = ContextPack(current_message="x")

        def replacement(synthesis, pack):
            return DiscussionSynthesis(
                mode=synthesis.mode,
                rationale="LLM 재합성",
                response_text="LLM 본문",
                next_actions=("LLM 다음 행동",),
            )

        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.DISCUSSION),
            llm_synthesizer=replacement,
        )
        self.assertEqual(synth.response_text, "LLM 본문")
        self.assertEqual(synth.next_actions, ("LLM 다음 행동",))

    def test_to_dict_serialises_all_fields(self) -> None:
        pack = ContextPack(current_message="API patch")
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.IMPLEMENTATION_CANDIDATE),
        )
        payload = synth.to_dict()
        self.assertEqual(payload["mode"], "implementation_candidate")
        self.assertTrue(payload["implementation_ready"])
        self.assertIn("response_text", payload)


if __name__ == "__main__":
    unittest.main()
