"""Integration tests for ``discord/engineering_discussion_turn.py``.

분류 → context pack → synthesis → (필요 시) handoff까지 한 번에 호출하는
gateway 진입점이 4가지 모드 모두에서 일관된 envelope를 만들어내는지 검증.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.discussion import (
    ContextPackBuilder,
    DiscussionMode,
    GithubIssueRef,
    ObsidianNoteRef,
    RelevantMemorySelector,
    ThreadMessage,
)
from yule_orchestrator.discord.engineering_discussion_turn import (
    build_discussion_turn_response,
)


class BuildDiscussionTurnResponseTestCase(unittest.TestCase):
    def test_discussion_path_renders_role_perspectives(self) -> None:
        result = build_discussion_turn_response(
            message_text="이 구조 맞아? devops 관점에서 어떻게 풀지",
        )
        self.assertEqual(result.classification.mode, DiscussionMode.DISCUSSION)
        self.assertIn("devops", result.rendered_text.lower())
        self.assertIn("권한 제안", result.rendered_text)
        self.assertIsNone(result.handoff)

    def test_research_path_lists_followups(self) -> None:
        result = build_discussion_turn_response(
            message_text="일단 조사만 해줘 — auth 흐름 정리",
        )
        self.assertEqual(result.classification.mode, DiscussionMode.RESEARCH_ONLY)
        self.assertTrue(any("research profile" in f for f in result.synthesis.research_followups))
        self.assertIsNone(result.handoff)

    def test_implementation_path_includes_proposal_block(self) -> None:
        result = build_discussion_turn_response(
            message_text="Spring Security 인증 마이그 패치 작성, API 흐름도 조정해줘 / PR 올려",
        )
        self.assertEqual(
            result.classification.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE
        )
        self.assertIsNotNone(result.handoff)
        self.assertIsNotNone(result.handoff.proposal)
        self.assertIn("코딩 권한 제안", result.rendered_text)
        self.assertIn("backend-engineer", result.rendered_text)

    def test_clarification_path_asks_questions(self) -> None:
        result = build_discussion_turn_response(message_text="ㅎ")
        self.assertEqual(
            result.classification.mode, DiscussionMode.CLARIFICATION_NEEDED
        )
        self.assertGreater(len(result.synthesis.open_questions), 0)
        self.assertIsNone(result.handoff)

    def test_pack_seams_propagate_into_synthesis(self) -> None:
        builder = ContextPackBuilder(
            thread_loader=lambda sid: [
                ThreadMessage(role="user", content="이전에 같은 화면 hero 정리하던 흐름"),
                ThreadMessage(role="tech-lead", content="hero 정리 PR #99 닫혔음"),
            ],
            issue_loader=lambda q: [
                GithubIssueRef(number=42, title="hero hero", state="open"),
            ],
            note_loader=lambda q: [
                ObsidianNoteRef(
                    title="hero 회의록",
                    path="notes/hero.md",
                    summary="hero 의사결정",
                    tags=("frontend-engineer",),
                ),
            ],
            memory_selector=RelevantMemorySelector(min_score=0.0),
        )
        result = build_discussion_turn_response(
            message_text="이 hero 구조 맞아?",
            session=SimpleNamespace(
                session_id="abc12345",
                task_type="frontend-feature",
                write_requested=False,
                write_blocked_reason=None,
                extra={"research_pack": {"x": 1}},
            ),
            builder=builder,
        )
        self.assertEqual(result.classification.mode, DiscussionMode.DISCUSSION)
        self.assertIn("issue #42", result.rendered_text)
        self.assertIn("hero 회의록", result.rendered_text)

    def test_seam_failure_surfaces_blocker_but_does_not_crash(self) -> None:
        def crash(_):
            raise RuntimeError("offline")

        builder = ContextPackBuilder(
            issue_loader=crash,
            pr_loader=crash,
        )
        result = build_discussion_turn_response(
            message_text="이 구조 맞아?",
            builder=builder,
        )
        self.assertEqual(result.classification.mode, DiscussionMode.DISCUSSION)
        self.assertTrue(any("issue_loader" in b for b in result.blockers))
        self.assertTrue(any("pr_loader" in b for b in result.blockers))

    def test_llm_seam_takes_over_on_ambiguous_text(self) -> None:
        # deterministic 신호가 약한 메시지 — LLM seam이 분류를 먹는다.
        result = build_discussion_turn_response(
            message_text="users 흐름 관련해서 한 번 봐줄래",
            llm_classifier=lambda **_: DiscussionMode.RESEARCH_ONLY,
        )
        self.assertEqual(result.classification.mode, DiscussionMode.RESEARCH_ONLY)


if __name__ == "__main__":
    unittest.main()
