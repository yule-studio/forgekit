from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import (
    BackendEngineerTake,
    ProductDesignerTake,
    TechLeadOpening,
    TechLeadSynthesis,
)
from yule_orchestrator.agents.obsidian.knowledge_writer import (
    KNOWLEDGE_CONTRACT_VERSION,
    KNOWLEDGE_KIND,
    KNOWLEDGE_SUBDIR,
    KnowledgeNote,
    build_knowledge_note,
    derive_knowledge_title,
    render_knowledge_note,
    scrub_title_text,
)
from yule_orchestrator.agents.obsidian.export import (
    DEFAULT_PROJECT,
    PROJECTS_BASE,
    render_research_note,
)
from yule_orchestrator.agents.research.pack import (
    ResearchAttachment,
    ResearchPack,
    ResearchSource,
    pack_from_discord_message,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState


def _session(**overrides) -> WorkflowSession:
    base = dict(
        session_id="abc12345",
        prompt="Hermes 코드베이스를 우리 에이전트 런타임에 통합할 수 있는지 검토",
        task_type="research",
        state=WorkflowState.APPROVED,
        created_at=datetime(2026, 5, 5, 9, 0),
        updated_at=datetime(2026, 5, 5, 9, 5),
        executor_role="ai-engineer",
    )
    base.update(overrides)
    return WorkflowSession(**base)


class ScrubTitleTextTests(unittest.TestCase):
    def test_strips_research_prefix_and_bold(self) -> None:
        self.assertEqual(
            scrub_title_text("[Research] **Hermes 통합 검토**"),
            "Hermes 통합 검토",
        )

    def test_strips_url_and_filler(self) -> None:
        # Real-world failure mode: the title was being generated as the
        # body's "자료 링크" header followed by the first URL — scrub
        # must produce a clean, semantic phrase.
        cleaned = scrub_title_text("자료 링크 https://github.com/example/hermes")
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("자료 링크", cleaned)
        self.assertEqual(cleaned, "")

    def test_strips_korean_filler_prefixes(self) -> None:
        cleaned = scrub_title_text("오늘은 Hermes 통합을 검토합니다")
        self.assertNotIn("오늘은", cleaned)
        self.assertIn("Hermes 통합", cleaned)


class DeriveKnowledgeTitleTests(unittest.TestCase):
    def test_explicit_session_topic_wins(self) -> None:
        session = _session(extra={"topic": "Hermes 에이전트 통합 리서치"})
        title = derive_knowledge_title(session=session)
        self.assertEqual(title, "Hermes 에이전트 통합 리서치")

    def test_url_only_pack_title_does_not_become_title(self) -> None:
        # Regression: prior behaviour produced "자료 링크 https://..."
        # as the H1 because the URL bled in from the body.
        pack = ResearchPack(
            title="자료 링크 https://github.com/example/hermes",
            summary="",
        )
        session = _session(prompt="")
        title = derive_knowledge_title(pack=pack, session=session)
        self.assertNotIn("https://", title)
        self.assertNotIn("자료 링크", title)
        # Falls all the way through to the task_type fallback because no
        # other usable signal exists.
        self.assertTrue(title.endswith("작업 정리"), title)

    def test_hermes_prompt_yields_short_semantic_title(self) -> None:
        session = _session(
            prompt=(
                "Hermes 에이전트 통합 리서치 — 우리 코드베이스에 어떻게 적용할 수 "
                "있을지 살펴보고, 다음 단계로 무엇을 정리할지 고민해보려 합니다."
            ),
        )
        title = derive_knowledge_title(session=session)
        self.assertLessEqual(len(title), 60)
        self.assertIn("Hermes", title)
        # Filler suffixes are gone.
        self.assertNotIn("고민해보려", title)
        self.assertNotIn("https://", title)

    def test_synthesis_title_used_when_pack_and_prompt_empty(self) -> None:
        synthesis = TechLeadSynthesis(
            consensus="Claude Code 기반 에이전트 런타임 참고 자료를 정리한다",
        )
        title = derive_knowledge_title(
            session=_session(prompt=""), synthesis=synthesis
        )
        self.assertIn("Claude Code", title)

    def test_pack_title_is_used_when_intentional(self) -> None:
        pack = ResearchPack(title="Stripe Pricing 패턴", summary="")
        title = derive_knowledge_title(
            pack=pack, session=_session(prompt="")
        )
        self.assertEqual(title, "Stripe Pricing 패턴")

    def test_task_type_fallback_when_nothing_useful(self) -> None:
        title = derive_knowledge_title(session=_session(prompt=""))
        self.assertTrue(title.endswith("작업 정리"))
        self.assertIn("research", title)


class BuildKnowledgeNoteTests(unittest.TestCase):
    def _pack(self) -> ResearchPack:
        return pack_from_discord_message(
            title="Hermes 통합 자료",
            content=(
                "Hermes 코드베이스 https://github.com/example/hermes 참고. "
                "우리 런타임에 적용 가능성 검토."
            ),
            author_role="engineering-agent/ai-engineer",
            channel_id=999,
            thread_id=888,
            message_id=777,
            posted_at=datetime(2026, 5, 5, 9, 0),
            attachments=[
                ResearchAttachment(
                    kind="image",
                    url="https://cdn/hermes.png",
                    filename="hermes-arch.png",
                )
            ],
            tags=("research",),
        )

    def test_minimal_pack_only_produces_well_formed_note(self) -> None:
        # research_pack alone is enough — synthesis and role turns are
        # optional and the note still surfaces every required section.
        note = build_knowledge_note(pack=self._pack(), session=_session())
        self.assertIsInstance(note, KnowledgeNote)
        # Title is short and free of URL noise.
        self.assertLessEqual(len(note.title), 60)
        self.assertNotIn("https://", note.title)
        # Required sections all present, in contract order.
        section_names = [name for name, _ in note.body_sections]
        self.assertEqual(
            section_names,
            [
                "작업 목적",
                "원문 요청",
                "현재 결론",
                "수집 자료",
                "역할별 검토",
                "Tech Lead 종합",
                "결정 / 제안",
                "다음 액션",
                "관련 세션",
            ],
        )
        # Frontmatter contract.
        for key in (
            "title",
            "topic",
            "original_prompt",
            "session_id",
            "kind",
            "status",
            "roles",
            "sources",
            "created_at",
            "task_type",
            "project",
        ):
            self.assertIn(key, note.frontmatter, key)
        self.assertEqual(note.frontmatter["kind"], KNOWLEDGE_KIND)
        self.assertEqual(note.frontmatter["contract"], KNOWLEDGE_CONTRACT_VERSION)
        # Path lands under the knowledge subdir.
        self.assertTrue(
            note.vault_folder.endswith(f"/{KNOWLEDGE_SUBDIR}"),
            note.vault_folder,
        )
        self.assertTrue(note.vault_filename.startswith("knowledge-"))

    def test_original_prompt_preserved_in_frontmatter_and_body(self) -> None:
        session = _session(
            prompt="Hermes 에이전트 통합 리서치 — 우리 코드베이스에 어떻게 적용할지 검토"
        )
        note = build_knowledge_note(pack=self._pack(), session=session)
        self.assertEqual(
            note.frontmatter["original_prompt"], session.prompt
        )
        body_map = dict(note.body_sections)
        self.assertIn(session.prompt, body_map["원문 요청"])

    def test_role_turns_included_in_review_section(self) -> None:
        pack = self._pack()
        opening = TechLeadOpening(
            task_breakdown=("Hermes 코드 구조 파악",),
            decisions_needed=("적용 범위 결정",),
            perspective="우선 architecture 파악이 필요",
            evidence=("hermes README 확인",),
            risks=("scope creep",),
            next_actions=("핵심 모듈 정리",),
        )
        designer = ProductDesignerTake(
            ux_direction="UX 영향 적음",
            perspective="에이전트 UX는 동일 유지",
            risks=("학습 곡선",),
            next_actions=("도큐먼트 다이어그램 추가",),
        )
        backend = BackendEngineerTake(
            data_impact="런타임 메모리 사용량 증가 가능",
            api_impact="dispatcher 인터페이스 영향",
            perspective="dispatcher 통합점 설계 필요",
            risks=("동시성",),
            next_actions=("dispatcher adapter 프로토타입",),
        )
        note = build_knowledge_note(
            pack=pack,
            session=_session(),
            role_turns=(opening, designer, backend),
        )
        body_map = dict(note.body_sections)
        review = body_map["역할별 검토"]
        self.assertIn("tech-lead", review)
        self.assertIn("product-designer", review)
        self.assertIn("backend-engineer", review)
        self.assertIn("Hermes 코드 구조 파악", review)
        next_actions = body_map["다음 액션"]
        self.assertIn("핵심 모듈 정리", next_actions)
        self.assertIn("dispatcher adapter 프로토타입", next_actions)
        decisions = body_map["결정 / 제안"]
        self.assertIn("적용 범위 결정", decisions)

    def test_synthesis_renders_into_tech_lead_section(self) -> None:
        synthesis = TechLeadSynthesis(
            consensus="Hermes의 dispatcher 패턴을 부분 적용",
            todos=("dispatcher adapter 시제품", "런타임 회귀 검증"),
            open_research=("성능 측정 baseline",),
            user_decisions_needed=("적용 범위 확정",),
            approval_required=False,
        )
        note = build_knowledge_note(
            pack=self._pack(), session=_session(), synthesis=synthesis
        )
        body_map = dict(note.body_sections)
        tech_lead = body_map["Tech Lead 종합"]
        self.assertIn("Hermes의 dispatcher 패턴을 부분 적용", tech_lead)
        self.assertIn("dispatcher adapter 시제품", tech_lead)
        self.assertIn("성능 측정 baseline", tech_lead)
        self.assertIn("승인 필요:** no", tech_lead)
        # Decisions section pulls from synthesis user_decisions_needed.
        self.assertIn("적용 범위 확정", body_map["결정 / 제안"])

    def test_status_reflects_synthesis_and_session(self) -> None:
        # Approval-pending wins.
        note = build_knowledge_note(
            pack=self._pack(),
            session=_session(),
            synthesis=TechLeadSynthesis(
                consensus="x",
                approval_required=True,
                approval_reason="쓰기 승인",
            ),
        )
        self.assertEqual(note.frontmatter["status"], "approval-pending")
        # Decided when not pending.
        decided = build_knowledge_note(
            pack=self._pack(),
            session=_session(),
            synthesis=TechLeadSynthesis(consensus="x", approval_required=False),
        )
        self.assertEqual(decided.frontmatter["status"], "decided")
        # Captured when no synthesis and intake state.
        captured = build_knowledge_note(
            pack=self._pack(),
            session=_session(state=WorkflowState.INTAKE),
        )
        self.assertEqual(captured.frontmatter["status"], "captured")


class RenderKnowledgeNoteTests(unittest.TestCase):
    def _pack(self) -> ResearchPack:
        return pack_from_discord_message(
            title="Hermes 통합 자료",
            content=(
                "Hermes 코드 https://github.com/example/hermes 참고. "
                "우리 런타임에 적용 가능성 검토."
            ),
            author_role="engineering-agent/ai-engineer",
            channel_id=10,
            thread_id=20,
            message_id=30,
            posted_at=datetime(2026, 5, 5, 9, 0),
            tags=("research",),
        )

    def test_render_research_note_with_kind_knowledge_routes_to_writer(self) -> None:
        # Public surface: the existing render_research_note() entry point
        # opt-ins to the knowledge writer when ``kind="knowledge"`` is
        # passed, so callers don't need to import knowledge_writer.
        session = _session(
            prompt="Hermes 에이전트 통합 리서치 — 우리 코드베이스에 어떻게 적용할지 검토"
        )
        synthesis = TechLeadSynthesis(
            consensus="Hermes의 dispatcher 패턴을 부분 적용",
            todos=("dispatcher adapter 프로토타입",),
            open_research=("성능 baseline",),
            user_decisions_needed=("적용 범위 확정",),
        )
        note = render_research_note(
            self._pack(),
            session=session,
            synthesis=synthesis,
            kind="knowledge",
            env={},
        )
        self.assertIn(
            f"{PROJECTS_BASE}/{DEFAULT_PROJECT}/{KNOWLEDGE_SUBDIR}",
            note.path.folder,
        )
        for header in (
            "## 작업 목적",
            "## 원문 요청",
            "## 현재 결론",
            "## 수집 자료",
            "## 역할별 검토",
            "## Tech Lead 종합",
            "## 결정 / 제안",
            "## 다음 액션",
            "## 관련 세션",
        ):
            self.assertIn(header, note.content)
        self.assertIn("kind: knowledge", note.content)
        self.assertIn("Hermes", note.content)
        # Original prompt survives into the body.
        self.assertIn("Hermes 에이전트 통합 리서치", note.content)
        # No URL leaks into the H1.
        h1 = note.content.split("\n#", 1)[0]
        self.assertNotIn("https://", h1.split("---", 2)[2] if h1.count("---") >= 2 else h1)

    def test_render_writes_under_session_project_when_set(self) -> None:
        session = _session(extra={"project": "yule-studio-agent"})
        note = render_knowledge_note(
            pack=self._pack(),
            session=session,
            env={},
        )
        self.assertIn(
            f"{PROJECTS_BASE}/yule-studio-agent/{KNOWLEDGE_SUBDIR}",
            note.path.folder,
        )
        self.assertIn("project: yule-studio-agent", note.content)

    def test_render_filename_basename_caps_at_100_chars(self) -> None:
        long_pack = ResearchPack(
            title=(
                "Hermes 에이전트 통합 리서치를 위한 매우 긴 후보 제목 "
                "정리 작업의 모든 세부 단계를 한 줄로 적어둔 케이스"
            ),
            summary="",
            created_at=datetime(2026, 5, 5),
        )
        note = render_knowledge_note(pack=long_pack, session=_session(prompt=""))
        self.assertLessEqual(len(note.path.filename), 100)
        self.assertTrue(note.path.filename.endswith(".md"))


class KnowledgeNoteSectionFormatTests(unittest.TestCase):
    def test_empty_role_turns_section_renders_with_explanatory_marker(self) -> None:
        note = build_knowledge_note(
            pack=ResearchPack(title="간단 메모", summary=""),
            session=_session(),
        )
        body_map = dict(note.body_sections)
        self.assertIn("기록되지 않았습니다", body_map["역할별 검토"])

    def test_decisions_and_actions_dedup_across_inputs(self) -> None:
        # Same decision/action coming from multiple sources should not
        # appear twice in the rendered list.
        opening = TechLeadOpening(decisions_needed=("적용 범위 결정",))
        synthesis = TechLeadSynthesis(
            consensus="x",
            user_decisions_needed=("적용 범위 결정",),
            todos=("dispatcher adapter",),
        )
        backend = BackendEngineerTake(
            next_actions=("dispatcher adapter",),
        )
        note = build_knowledge_note(
            pack=None,
            session=_session(),
            synthesis=synthesis,
            role_turns=(opening, backend),
        )
        body_map = dict(note.body_sections)
        self.assertEqual(body_map["결정 / 제안"].count("적용 범위 결정"), 1)
        self.assertEqual(body_map["다음 액션"].count("dispatcher adapter"), 1)


if __name__ == "__main__":
    unittest.main()
