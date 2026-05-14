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
from yule_orchestrator.discord.engineering.discussion_turn import (
    OPERATOR_STATE_BLOCKED,
    OPERATOR_STATE_CLARIFICATION,
    OPERATOR_STATE_NEEDS_APPROVAL,
    OPERATOR_STATE_RESEARCH_PENDING,
    OPERATOR_STATE_RETRY_READY,
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


class OperatorStatusSurfaceTestCase(unittest.TestCase):
    """``DiscussionTurnResponse.operator_status`` 가 4 모드 × handoff
    결과 별로 운영자 surface 라우팅에 맞게 정렬되는지."""

    def test_clarification_marks_user_action(self) -> None:
        result = build_discussion_turn_response(message_text="ㅎ")
        self.assertEqual(
            result.operator_status["state"], OPERATOR_STATE_CLARIFICATION
        )
        self.assertEqual(result.operator_status["primary_actor"], "user")
        # 한국어 headline 한 줄.
        self.assertIn("clarification", result.operator_status["headline"])
        self.assertIsNone(result.operator_status["handoff_blocker_kind"])

    def test_research_state_for_research_only(self) -> None:
        result = build_discussion_turn_response(
            message_text="일단 조사만 해줘 — auth 흐름 정리",
        )
        self.assertEqual(
            result.operator_status["state"], OPERATOR_STATE_RESEARCH_PENDING
        )
        self.assertEqual(result.operator_status["primary_actor"], "tech-lead")

    def test_implementation_with_proposal_marks_needs_approval(self) -> None:
        result = build_discussion_turn_response(
            message_text="Spring Security 인증 마이그 패치 작성, API 흐름도 조정해줘 / PR 올려",
        )
        self.assertEqual(
            result.operator_status["state"], OPERATOR_STATE_NEEDS_APPROVAL
        )
        self.assertEqual(result.operator_status["primary_actor"], "user")
        self.assertIn("승인", result.operator_status["headline"])
        # proposal 이 만들어진 경우 handoff_blocker_kind 는 None.
        self.assertIsNone(result.operator_status["handoff_blocker_kind"])

    def test_research_conflict_surface_as_retry_ready(self) -> None:
        # 분류기는 implementation_candidate 으로 봤지만 권한 레이어가 본문
        # 의 "조사만" 신호로 인해 research-only 로 떨어뜨린 경우 — 운영자
        # 가 보았을 때 사용자가 한 번 더 의도를 알려주면 풀린다는 의미로
        # retry_ready 상태로 surface 되어야 한다. 분류기와 권한 레이어가
        # 서로 다른 시그널을 보는 분기를 시뮬레이션하기 위해 _build_operator_status
        # 를 직접 호출한다.
        from yule_orchestrator.discord.engineering.discussion_turn import (
            _build_operator_status,
        )
        from yule_orchestrator.agents.discussion import (
            ContextPack,
            DiscussionMode,
            DiscussionModeMatch,
            DiscussionSynthesis,
            build_implementation_handoff,
        )

        pack = ContextPack(current_message="조사만 해줘 — auth 흐름 정리")
        synth = DiscussionSynthesis(
            mode=DiscussionMode.IMPLEMENTATION_CANDIDATE,
            rationale="impl by hand",
            response_text="x",
            implementation_ready=True,
            escalation_state="implementation_ready",
            primary_actor="user",
        )
        handoff = build_implementation_handoff(synthesis=synth, pack=pack)
        self.assertIsNone(handoff.proposal)
        assert handoff.blocker is not None
        self.assertEqual(handoff.blocker.kind, "research_only_conflict")

        status = _build_operator_status(
            classification=DiscussionModeMatch(
                mode=DiscussionMode.IMPLEMENTATION_CANDIDATE,
                rationale="impl by hand",
                signals=(),
                source="deterministic",
                confidence="high",
            ),
            synthesis=synth,
            handoff=handoff,
            blockers=("권한 추천이 research-only",),
        )
        # retry_ready 상태로 명확히 surface — 사용자가 추가 phrase 만 던지면
        # 풀린다는 의미.
        self.assertEqual(status["state"], OPERATOR_STATE_RETRY_READY)
        self.assertEqual(status["primary_actor"], "user")
        self.assertEqual(status["handoff_blocker_kind"], "research_only_conflict")
        self.assertIn("research-only", status["headline"])

    def test_pack_blocker_in_discussion_mode_flags_blocked(self) -> None:
        def crash(_):
            raise RuntimeError("offline")

        builder = ContextPackBuilder(issue_loader=crash, pr_loader=crash)
        result = build_discussion_turn_response(
            message_text="이 구조 맞아? backend 관점에서",
            builder=builder,
        )
        # discussion 모드지만 pack 에 blocker 가 있으면 operator 가 보아야 한다.
        self.assertEqual(
            result.operator_status["state"], OPERATOR_STATE_BLOCKED
        )
        self.assertEqual(
            result.operator_status["primary_actor"], "operator"
        )
        # 블록 사유들이 status surface 에도 들어간다.
        self.assertTrue(
            any("issue_loader" in b for b in result.operator_status["blockers"])
        )


class HeaderAndBoundaryTestCase(unittest.TestCase):
    """response_text 가 새 header 로 시작하는지 + gateway 가 토의 흐름의
    한 단위로 sole-source 로 사용할 수 있는지."""

    def test_response_text_starts_with_mode_header(self) -> None:
        result = build_discussion_turn_response(
            message_text="이 구조 맞아? devops 관점",
        )
        # 첫 줄에 mode label.
        first_lines = result.rendered_text.splitlines()[:2]
        self.assertIn("**모드:** 토의", first_lines[0])
        # 두 번째 줄 분류기 메타데이터.
        self.assertIn("분류기:", first_lines[1])

    def test_implementation_role_check_bullets_in_body(self) -> None:
        # backend 키워드 → backend-engineer 관점 헤드라인 + 3 체크 bullet.
        result = build_discussion_turn_response(
            message_text="이 구조 맞아? backend API auth 흐름",
        )
        self.assertIn("backend-engineer", result.rendered_text)
        self.assertIn("스키마 변경 범위", result.rendered_text)


if __name__ == "__main__":
    unittest.main()
