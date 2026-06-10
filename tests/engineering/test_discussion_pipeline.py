"""End-to-end integration: classify → context_pack → synthesize → handoff.

마스터 플랜 §13 Phase 1 완료 기준은 "Discord에서 기술 토의 가능 + context
pack 구성 + implementation 여부 판단". 본 테스트는 4가지 사용자 시나리오를
실제 호출 체인으로 돌려서 합성 결과 + handoff payload가 일관되게 나오는지
잠근다. 외부 I/O (Discord/GitHub/Obsidian)는 stub seam으로 대체한다.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.authorization import reset_role_profile_cache
from yule_engineering.agents.discussion import (
    ContextPackBuilder,
    DiscussionMode,
    GithubIssueRef,
    GithubPRRef,
    ObsidianNoteRef,
    RelevantMemorySelector,
    ThreadMessage,
    build_implementation_handoff,
    classify_discussion_mode,
    synthesize_discussion,
)


def _session(extra=None):
    return SimpleNamespace(
        session_id="sess-xyz",
        task_type="backend-feature",
        write_requested=False,
        write_blocked_reason=None,
        extra=extra or {},
    )


def _builder() -> ContextPackBuilder:
    """Live retrieval seam을 모두 stub로 채운 builder."""

    return ContextPackBuilder(
        thread_loader=lambda sid: [
            ThreadMessage(role="user", content="이전에 hero 영역 정리 얘기 있었음"),
        ],
        issue_loader=lambda q: [
            GithubIssueRef(number=42, title=f"{q[:20]} 회귀", state="open"),
        ],
        pr_loader=lambda q: [
            GithubPRRef(number=99, title=f"{q[:20]} 시도 1", state="closed"),
        ],
        note_loader=lambda q: [
            ObsidianNoteRef(
                title="auth migration 회고",
                summary=f"이전 {q[:30]} 정리 메모 — PR #42 참고",
                tags=("auth", "backend-engineer"),
                kind="retrospective",
                updated_at="2026-04-01T00:00:00",
            ),
            ObsidianNoteRef(
                title="다른 프로젝트",
                summary="lunch was good",
                tags=("personal",),
            ),
        ],
        memory_selector=RelevantMemorySelector(min_score=0.5),
        code_hint_loader=lambda q: [],
        role_profile_loader=lambda role: f"profile for {role}",
        role_research_profile_loader=lambda role: f"research for {role}",
    )


class DiscussionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        reset_role_profile_cache()

    def _run(self, message: str, *, role: str = "engineering-agent/tech-lead"):
        builder = _builder()
        pack = builder.build(
            message_text=message,
            session=_session(),
            role_for_research=role,
        )
        match = classify_discussion_mode(message, context_pack=pack.as_dict())
        synthesis = synthesize_discussion(pack=pack, classification=match)
        handoff = build_implementation_handoff(synthesis=synthesis, pack=pack)
        return pack, match, synthesis, handoff

    def test_design_question_routes_to_discussion_no_handoff(self) -> None:
        pack, match, synth, handoff = self._run(
            "이 구조 맞아? backend 관점에서 어떻게 풀지 같이 보자"
        )
        self.assertEqual(match.mode, DiscussionMode.DISCUSSION)
        self.assertFalse(synth.implementation_ready)
        self.assertIn("backend-engineer", synth.role_perspectives)
        # handoff는 not-impl mode → blocker.
        self.assertIsNone(handoff.proposal)
        # context pack 자체에는 thread/issue/pr/note가 모두 채워졌다.
        self.assertGreater(len(pack.recent_thread), 0)
        self.assertGreater(len(pack.related_issues), 0)
        self.assertGreater(len(pack.related_prs), 0)
        # auth migration 회고 note만 통과 (relevant memory selector)
        self.assertEqual(len(pack.relevant_notes), 1)
        self.assertEqual(pack.relevant_notes[0].title, "auth migration 회고")

    def test_research_request_routes_to_research_only(self) -> None:
        pack, match, synth, handoff = self._run("일단 조사만 할까")
        self.assertEqual(match.mode, DiscussionMode.RESEARCH_ONLY)
        self.assertFalse(synth.implementation_ready)
        self.assertIn("운영-리서치", synth.response_text)
        self.assertIsNone(handoff.proposal)

    def test_implementation_request_routes_and_creates_proposal(self) -> None:
        pack, match, synth, handoff = self._run(
            "Spring Security 기반 API auth 흐름 구현 진행해줘"
        )
        self.assertEqual(match.mode, DiscussionMode.IMPLEMENTATION_CANDIDATE)
        self.assertTrue(synth.implementation_ready)
        # handoff는 정상적으로 proposal 생성
        self.assertIsNotNone(handoff.proposal)
        self.assertEqual(handoff.proposal.executor_role, "backend-engineer")
        self.assertEqual(handoff.proposal.session_id, "sess-xyz")
        self.assertTrue(handoff.proposal.approval_required)
        # response_text에 "권한 제안" 안내가 포함됨
        self.assertIn("권한 제안", synth.response_text)

    def test_too_short_message_routes_to_clarification(self) -> None:
        pack, match, synth, handoff = self._run("?")
        self.assertEqual(match.mode, DiscussionMode.CLARIFICATION_NEEDED)
        self.assertGreater(len(synth.open_questions), 0)
        self.assertIsNone(handoff.proposal)
        # handoff blocker는 not-impl mode
        self.assertIsNotNone(handoff.blocker)


if __name__ == "__main__":
    unittest.main()
