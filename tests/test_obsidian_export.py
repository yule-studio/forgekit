from __future__ import annotations

import unittest
from datetime import date, datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import TechLeadSynthesis
from yule_orchestrator.agents.obsidian_export import (
    CONTRACT_VERSION,
    PATH_DECISIONS,
    PATH_REFERENCES,
    PATH_RESEARCH,
    ExportPath,
    recommend_path,
    render_research_note,
)
from yule_orchestrator.agents.research_pack import (
    ResearchAttachment,
    ResearchSource,
    ResearchPack,
    pack_from_discord_message,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState


def _session(**overrides) -> WorkflowSession:
    base = dict(
        session_id="abc12345",
        prompt="hero 정리",
        task_type="landing-page",
        state=WorkflowState.APPROVED,
        created_at=datetime(2026, 4, 30, 9, 0),
        updated_at=datetime(2026, 4, 30, 9, 5),
        executor_role="frontend-engineer",
        executor_runner="codex",
    )
    base.update(overrides)
    return WorkflowSession(**base)


class RecommendPathTestCase(unittest.TestCase):
    def test_research_default(self) -> None:
        path = recommend_path(
            title="Stripe Pricing 패턴",
            kind="research",
            created_at=datetime(2026, 4, 30),
        )
        self.assertEqual(path.folder, PATH_RESEARCH)
        self.assertTrue(path.filename.startswith("2026-04-30_"))
        self.assertTrue(path.filename.endswith(".md"))

    def test_decisions_folder(self) -> None:
        path = recommend_path(title="x", kind="decision")
        self.assertEqual(path.folder, PATH_DECISIONS)

    def test_references_folder(self) -> None:
        path = recommend_path(title="x", kind="references")
        self.assertEqual(path.folder, PATH_REFERENCES)

    def test_unknown_kind_falls_back(self) -> None:
        path = recommend_path(title="x", kind="diary")
        self.assertEqual(path.folder, PATH_RESEARCH)

    def test_korean_title_is_slugified(self) -> None:
        path = recommend_path(title="히어로 섹션 정리 v2", kind="research")
        self.assertIn("히어로", path.filename)
        self.assertNotIn(" ", path.filename)

    def test_blank_title_yields_untitled(self) -> None:
        path = recommend_path(title="   ", kind="research", created_at=datetime(2026, 4, 30))
        self.assertEqual(path.filename, "2026-04-30_untitled.md")


class RenderNoteTestCase(unittest.TestCase):
    def _pack(self) -> ResearchPack:
        return pack_from_discord_message(
            title="Stripe Pricing 패턴",
            content="hero step copy 강조 — https://stripe.com/pricing 참고",
            author_role="engineering-agent/product-designer",
            channel_id=999,
            thread_id=888,
            message_id=777,
            posted_at=datetime(2026, 4, 30, 9, 0),
            attachments=[
                ResearchAttachment(
                    kind="image",
                    url="https://cdn/x.png",
                    filename="hero.png",
                )
            ],
            tags=["reference", "ux"],
        )

    def test_renders_research_note_without_synthesis(self) -> None:
        note = render_research_note(self._pack())
        self.assertEqual(note.path.folder, PATH_RESEARCH)
        self.assertIn("contract: research-forum-export/v0", note.content)
        self.assertIn("title: Stripe Pricing 패턴", note.content)
        self.assertIn("source: https://stripe.com/pricing", note.content)
        self.assertIn("# Stripe Pricing 패턴", note.content)
        self.assertIn("## 자료 링크", note.content)
        self.assertIn("## 첨부", note.content)
        self.assertIn("`image`", note.content)

    def test_decision_note_when_synthesis_provided(self) -> None:
        synth = TechLeadSynthesis(
            consensus="hero 카피 정리, 모바일 반응형 보정",
            todos=("CTA 색 정리", "h1 라인높이 통일"),
            open_research=("reference 추가 수집",),
            user_decisions_needed=("브랜드 톤 결정",),
            approval_required=True,
            approval_reason="write requires approval",
        )
        note = render_research_note(self._pack(), session=_session(), synthesis=synth)
        self.assertEqual(note.path.folder, PATH_DECISIONS)
        self.assertIn("status: approval-pending", note.content)
        self.assertIn("approval_required: true", note.content)
        self.assertIn("## 합의안", note.content)
        self.assertIn("hero 카피 정리, 모바일 반응형 보정", note.content)
        self.assertIn("## 해야 할 일", note.content)
        self.assertIn("- CTA 색 정리", note.content)
        self.assertIn("## 더 조사할 것", note.content)
        self.assertIn("## 사용자 결정 필요", note.content)
        self.assertIn("승인 필요 여부\nyes — write requires approval", note.content)

    def test_explicit_reference_kind(self) -> None:
        note = render_research_note(self._pack(), kind="reference")
        self.assertEqual(note.path.folder, PATH_REFERENCES)
        self.assertIn("kind: reference", note.content)
        # tag derived from kind = "reference" (singular)
        self.assertIn("tags: [reference, ux]", note.content)

    def test_status_decided_without_approval(self) -> None:
        synth = TechLeadSynthesis(
            consensus="끝",
            approval_required=False,
        )
        note = render_research_note(self._pack(), synthesis=synth)
        self.assertIn("status: decided", note.content)

    def test_status_captured_when_intake(self) -> None:
        note = render_research_note(
            self._pack(),
            session=_session(state=WorkflowState.INTAKE),
        )
        self.assertIn("status: captured", note.content)

    def test_session_meta_block(self) -> None:
        note = render_research_note(self._pack(), session=_session())
        self.assertIn("## 메타", note.content)
        self.assertIn("session_id: `abc12345`", note.content)
        self.assertIn("task_type: `landing-page`", note.content)
        self.assertIn("executor_role: `frontend-engineer`", note.content)
        self.assertIn("session_id: abc12345", note.content)  # frontmatter

    def test_frontmatter_contains_roles_from_pack(self) -> None:
        note = render_research_note(self._pack())
        # roles list pulled from pack.author_roles
        self.assertIn("roles: [engineering-agent/product-designer]", note.content)

    def test_no_url_no_link_block(self) -> None:
        pack = ResearchPack(title="회의록", summary="짧은 메모")
        note = render_research_note(pack)
        self.assertNotIn("## 자료 링크", note.content)
        self.assertNotIn("## 첨부", note.content)
        self.assertIn("## 요약", note.content)
        # source frontmatter is null
        self.assertIn("source: null", note.content)

    def test_exported_at_appears_when_provided(self) -> None:
        note = render_research_note(
            self._pack(),
            exported_at=datetime(2026, 5, 1, 12, 0),
        )
        self.assertIn("exported_at: 2026-05-01T12:00:00", note.content)


class FrontmatterShapeTestCase(unittest.TestCase):
    def _pack(self) -> ResearchPack:
        return pack_from_discord_message(
            title="Stripe Pricing 패턴",
            content="hero step copy 강조 — https://stripe.com/pricing 참고",
            author_role="engineering-agent/product-designer",
            channel_id=999,
            thread_id=888,
            message_id=777,
            posted_at=datetime(2026, 4, 30, 9, 0),
            attachments=[
                ResearchAttachment(
                    kind="image",
                    url="https://cdn/x.png",
                    filename="hero.png",
                )
            ],
            tags=["reference", "ux"],
        )

    def test_yaml_keys_in_expected_order(self) -> None:
        pack = pack_from_discord_message(title="t", content="https://x")
        note = render_research_note(pack)
        head = note.content.split("---", 2)[1]
        order = []
        for line in head.strip().splitlines():
            if ":" in line:
                order.append(line.split(":", 1)[0].strip())
        self.assertEqual(
            order[:11],
            [
                "title",
                "source",
                "roles",
                "status",
                "session_id",
                "created_at",
                "kind",
                "tags",
                "topic",
                "task_type",
                "sources",
            ],
        )

    def test_frontmatter_dict_carries_contract_version(self) -> None:
        pack = pack_from_discord_message(title="t", content="x")
        note = render_research_note(pack)
        self.assertEqual(note.frontmatter["contract"], CONTRACT_VERSION)
        self.assertEqual(note.frontmatter["kind"], "research")

    def test_frontmatter_topic_mirrors_pack_title(self) -> None:
        note = render_research_note(self._pack())
        self.assertEqual(note.frontmatter["topic"], "Stripe Pricing 패턴")
        self.assertIn("topic: Stripe Pricing 패턴", note.content)

    def test_frontmatter_task_type_from_session(self) -> None:
        note = render_research_note(self._pack(), session=_session())
        self.assertEqual(note.frontmatter["task_type"], "landing-page")
        self.assertIn("task_type: landing-page", note.content)

    def test_frontmatter_task_type_null_without_session(self) -> None:
        note = render_research_note(self._pack())
        self.assertIsNone(note.frontmatter["task_type"])
        self.assertIn("task_type: null", note.content)

    def test_frontmatter_sources_includes_urls_and_attachments(self) -> None:
        note = render_research_note(self._pack())
        self.assertEqual(
            note.frontmatter["sources"],
            ["https://stripe.com/pricing", "https://cdn/x.png"],
        )
        self.assertIn(
            "sources: [https://stripe.com/pricing, https://cdn/x.png]",
            note.content,
        )

    def test_frontmatter_sources_empty_when_no_urls(self) -> None:
        pack = ResearchPack(title="회의록", summary="짧은 메모")
        note = render_research_note(pack)
        self.assertEqual(note.frontmatter["sources"], [])
        self.assertIn("sources: []", note.content)


class ProjectVaultLayoutTests(unittest.TestCase):
    def test_project_kw_routes_research_into_projects_tree(self) -> None:
        from yule_orchestrator.agents.obsidian_export import recommend_path

        path = recommend_path(
            title="Stripe pricing hero copy",
            kind="research",
            created_at=datetime(2026, 4, 30),
            project="yule-studio-agent",
        )
        self.assertTrue(path.folder.startswith("10-projects/yule-studio-agent/"))
        self.assertTrue(path.folder.endswith("/research"))

    def test_project_decision_uses_decisions_subdir(self) -> None:
        from yule_orchestrator.agents.obsidian_export import recommend_path

        path = recommend_path(
            title="hero copy 분할",
            kind="decision",
            created_at=datetime(2026, 4, 30),
            project="yule-studio-agent",
        )
        self.assertTrue(path.folder.endswith("/decisions"))

    def test_default_layout_unchanged_when_project_absent(self) -> None:
        from yule_orchestrator.agents.obsidian_export import (
            PATH_RESEARCH,
            recommend_path,
        )

        path = recommend_path(
            title="x", kind="research", created_at=datetime(2026, 4, 30)
        )
        self.assertEqual(path.folder, PATH_RESEARCH)

    def test_render_research_note_picks_up_session_project_extra(self) -> None:
        pack = ResearchPack(title="개발팀 학습 루프 설계")
        session = WorkflowSession(
            session_id="abc",
            prompt="x",
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 4, 30, 9, 0),
            updated_at=datetime(2026, 4, 30, 9, 0),
            extra={"project": "yule-studio-agent"},
        )
        note = render_research_note(pack, session=session)
        self.assertTrue(note.path.folder.startswith("10-projects/"))
        self.assertIn("project: yule-studio-agent", note.content)


if __name__ == "__main__":
    unittest.main()
