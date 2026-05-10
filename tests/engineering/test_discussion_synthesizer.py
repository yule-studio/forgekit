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
    EngineeringKnowledgeRef,
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
        # share_boundary 는 항상 dict — 빈 pack 이어도 4 키 carries.
        self.assertEqual(payload["share_boundary"]["total"], 0)
        self.assertEqual(payload["share_boundary"]["public"], 0)
        self.assertEqual(payload["knowledge_evidence_block"], "")
        self.assertEqual(payload["knowledge_short_summary"], "")


def _public_ref(**overrides) -> EngineeringKnowledgeRef:
    base = dict(
        title="Spring Security 인증 흐름",
        role="backend-engineer",
        topic_key="spring-auth",
        source_url="https://example.com/spring-auth",
        source_name="Spring Docs",
        summary="OAuth2 + Filter chain 정리",
        score=8.0,
        signals=("role_primary_match",),
        evidence_labels=("요청 역할과 정확히 일치",),
        share_scope="public",
    )
    base.update(overrides)
    return EngineeringKnowledgeRef(**base)


class SynthesizeKnowledgeEvidenceTestCase(unittest.TestCase):
    """``relevant_knowledge`` 가 응답 surface 에 그대로 흘러야 한다."""

    def test_discussion_appends_evidence_block_and_summary(self) -> None:
        pack = ContextPack(
            current_message="auth 흐름 정리하자",
            relevant_knowledge=(
                _public_ref(),
                _public_ref(
                    title="사내 OAuth playbook",
                    topic_key="company-oauth",
                    share_scope="team_internal",
                    summary="외부 surface 에는 노출 금지",
                ),
            ),
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.DISCUSSION),
        )
        # Response 본문에 evidence 블록 + 짧은 요약 한 줄이 모두 들어간다.
        self.assertIn("근거 자료", synth.response_text)
        self.assertIn("Spring Security 인증 흐름", synth.response_text)
        self.assertIn("team-internal", synth.response_text)
        # 짧은 요약 라인 — 운영자가 본문 펼치기 전에 한 눈에 본다.
        self.assertIn("근거 자료 2건", synth.response_text)
        # 별도 surface field 도 채워져 있다.
        self.assertIn("Spring Security", synth.knowledge_evidence_block)
        self.assertIn("근거 자료 2건", synth.knowledge_short_summary)
        self.assertEqual(synth.share_boundary["public"], 1)
        self.assertEqual(synth.share_boundary["team_internal"], 1)
        self.assertEqual(synth.share_boundary["total"], 2)
        # team_internal 자료의 본문 요약은 응답에 새지 않는다.
        self.assertNotIn(
            "외부 surface 에는 노출 금지", synth.response_text
        )

    def test_research_response_carries_existing_evidence_when_available(self) -> None:
        pack = ContextPack(
            current_message="auth 자료 모아 보자",
            relevant_knowledge=(_public_ref(),),
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.RESEARCH_ONLY),
        )
        self.assertIn("이미 모인 자료", synth.response_text)
        self.assertIn("이미 vault 에 있는 근거 자료", synth.response_text)
        self.assertIn("Spring Security 인증 흐름", synth.response_text)
        self.assertEqual(synth.share_boundary["total"], 1)

    def test_implementation_includes_decision_evidence_summary(self) -> None:
        pack = ContextPack(
            current_message="API auth 흐름 패치 작성, schema 마이그도 같이",
            relevant_knowledge=(
                _public_ref(),
                _public_ref(
                    title="incident-2026-04-29",
                    topic_key="incident-2026-04-29",
                    share_scope="restricted",
                    share_scope_reason="customer PII",
                ),
            ),
        )
        synth = synthesize_discussion(
            pack=pack,
            classification=_classification(DiscussionMode.IMPLEMENTATION_CANDIDATE),
        )
        self.assertIn("결정 근거 요약", synth.response_text)
        self.assertIn("이번 결정 근거 자료", synth.response_text)
        # restricted 자료는 제목 / URL 이 모두 마스킹되고 reason 만 노출.
        self.assertNotIn("internal.example", synth.response_text)
        self.assertIn("공개 제한된 자료", synth.response_text)
        self.assertIn("customer PII", synth.response_text)
        # share_boundary 는 restricted/public 둘 다 카운트.
        self.assertEqual(synth.share_boundary["restricted"], 1)
        self.assertEqual(synth.share_boundary["public"], 1)

    def test_short_summary_orders_scope_breakdown(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(
                _public_ref(),
                _public_ref(
                    title="internal-doc",
                    topic_key="internal-1",
                    share_scope="team_internal",
                ),
                _public_ref(
                    title="restricted-doc",
                    topic_key="restricted-1",
                    share_scope="restricted",
                    share_scope_reason="PII",
                ),
            ),
        )
        summary = pack.knowledge_short_summary()
        self.assertIn("근거 자료 3건", summary)
        # public → team_internal → restricted 순으로 축약 카운트가 들어간다.
        self.assertLess(summary.index("public"), summary.index("team_internal"))
        self.assertLess(
            summary.index("team_internal"), summary.index("restricted")
        )

    def test_share_boundary_breakdown_handles_unknown_scope(self) -> None:
        pack = ContextPack(
            current_message="auth",
            relevant_knowledge=(
                _public_ref(share_scope="weird-value"),
            ),
        )
        breakdown = pack.share_boundary_breakdown()
        # Unknown scopes fall back into the public bucket so the
        # boundary surface never silently drops items.
        self.assertEqual(breakdown["public"], 1)
        self.assertEqual(breakdown["total"], 1)


if __name__ == "__main__":
    unittest.main()
