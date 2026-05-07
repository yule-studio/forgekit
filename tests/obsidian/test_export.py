from __future__ import annotations

import unittest
from datetime import date, datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.deliberation import TechLeadSynthesis
from yule_orchestrator.agents.obsidian.export import (
    CONTRACT_VERSION,
    DEFAULT_PROJECT,
    INBOX_UNSORTED,
    LAYOUT_LEGACY_AGENT,
    LAYOUT_YULE_AGENT_VAULT,
    PATH_DECISIONS,
    PATH_REFERENCES,
    PATH_RESEARCH,
    PROJECT_DECISIONS_SUBDIR,
    PROJECT_REFERENCES_SUBDIR,
    PROJECT_RESEARCH_SUBDIR,
    PROJECTS_BASE,
    ExportPath,
    recommend_path,
    render_research_note,
)


def _default_project_folder(subdir: str, project: str = DEFAULT_PROJECT) -> str:
    return f"{PROJECTS_BASE}/{project}/{subdir}"
from yule_orchestrator.agents.research.pack import (
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
    """Default layout = yule-agent-vault, default project = yule-studio-agent.

    Tests pass ``env={}`` so ``OBSIDIAN_*`` variables on the developer's
    machine never bleed into expected paths.
    """

    def test_research_default_goes_to_default_project_research(self) -> None:
        path = recommend_path(
            title="Stripe Pricing 패턴",
            kind="research",
            created_at=datetime(2026, 4, 30),
            env={},
        )
        self.assertEqual(
            path.folder, _default_project_folder(PROJECT_RESEARCH_SUBDIR)
        )
        self.assertTrue(path.filename.startswith("2026-04-30_"))
        self.assertTrue(path.filename.endswith(".md"))

    def test_decision_default_goes_to_default_project_decisions(self) -> None:
        path = recommend_path(title="x", kind="decision", env={})
        self.assertEqual(
            path.folder, _default_project_folder(PROJECT_DECISIONS_SUBDIR)
        )

    def test_reference_default_goes_to_default_project_references(self) -> None:
        path = recommend_path(title="x", kind="references", env={})
        self.assertEqual(
            path.folder, _default_project_folder(PROJECT_REFERENCES_SUBDIR)
        )

    def test_unknown_kind_routes_to_inbox_unsorted(self) -> None:
        # Spec: unknown/ambiguous kinds land in 00-inbox/unsorted/ so the
        # operator can triage them instead of having them buried under
        # research/.
        path = recommend_path(title="x", kind="diary", env={})
        self.assertEqual(path.folder, INBOX_UNSORTED)

    def test_explicit_project_arg_overrides_default(self) -> None:
        path = recommend_path(
            title="x",
            kind="research",
            project="other-project",
            env={},
        )
        self.assertEqual(
            path.folder,
            _default_project_folder(
                PROJECT_RESEARCH_SUBDIR, project="other-project"
            ),
        )

    def test_env_default_project_takes_effect_when_arg_blank(self) -> None:
        path = recommend_path(
            title="x",
            kind="research",
            env={"OBSIDIAN_DEFAULT_PROJECT": "project-from-env"},
        )
        self.assertTrue(path.folder.startswith("10-projects/project-from-env/"))

    def test_legacy_layout_uses_agents_engineering_tree(self) -> None:
        path = recommend_path(
            title="x",
            kind="decision",
            layout="legacy-agent",
            env={},
        )
        self.assertEqual(path.folder, PATH_DECISIONS)

    def test_legacy_layout_via_env_var(self) -> None:
        path = recommend_path(
            title="x",
            kind="research",
            env={"OBSIDIAN_EXPORT_LAYOUT": "legacy-agent"},
        )
        self.assertEqual(path.folder, PATH_RESEARCH)

    def test_korean_title_is_slugified(self) -> None:
        path = recommend_path(title="히어로 섹션 정리 v2", kind="research", env={})
        self.assertIn("히어로", path.filename)
        self.assertNotIn(" ", path.filename)

    def test_blank_title_yields_untitled(self) -> None:
        path = recommend_path(
            title="   ",
            kind="research",
            created_at=datetime(2026, 4, 30),
            env={},
        )
        self.assertEqual(path.filename, "2026-04-30_research-untitled.md")


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
        # Default layout = yule-agent-vault → path lands under
        # 10-projects/<default-project>/research/.
        note = render_research_note(self._pack(), env={})
        self.assertEqual(
            note.path.folder, _default_project_folder(PROJECT_RESEARCH_SUBDIR)
        )
        self.assertIn("contract: research-forum-export/v0", note.content)
        self.assertIn("title: Stripe Pricing 패턴", note.content)
        self.assertIn("source: https://stripe.com/pricing", note.content)
        self.assertIn("# Stripe Pricing 패턴", note.content)
        self.assertIn("## 자료 링크", note.content)
        self.assertIn("## 첨부", note.content)
        self.assertIn("`image`", note.content)
        # Default project name is stamped into frontmatter so retrieval
        # can filter by project without re-reading the path.
        self.assertIn(f"project: {DEFAULT_PROJECT}", note.content)

    def test_decision_note_when_synthesis_provided(self) -> None:
        synth = TechLeadSynthesis(
            consensus="hero 카피 정리, 모바일 반응형 보정",
            todos=("CTA 색 정리", "h1 라인높이 통일"),
            open_research=("reference 추가 수집",),
            user_decisions_needed=("브랜드 톤 결정",),
            approval_required=True,
            approval_reason="write requires approval",
        )
        note = render_research_note(
            self._pack(), session=_session(), synthesis=synth, env={}
        )
        self.assertEqual(
            note.path.folder, _default_project_folder(PROJECT_DECISIONS_SUBDIR)
        )
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
        note = render_research_note(self._pack(), kind="reference", env={})
        self.assertEqual(
            note.path.folder, _default_project_folder(PROJECT_REFERENCES_SUBDIR)
        )
        self.assertIn("kind: reference", note.content)
        # tag derived from kind = "reference" (singular)
        self.assertIn("tags: [reference, ux]", note.content)

    def test_legacy_layout_writes_to_agents_engineering_research(self) -> None:
        note = render_research_note(
            self._pack(), layout=LAYOUT_LEGACY_AGENT, env={}
        )
        self.assertEqual(note.path.folder, PATH_RESEARCH)
        # Legacy mode keeps frontmatter project-less so byte output stays
        # stable for vaults that haven't migrated.
        self.assertNotIn("project:", note.content)

    def test_legacy_layout_via_env_var_writes_to_agents_engineering(self) -> None:
        note = render_research_note(
            self._pack(),
            env={"OBSIDIAN_EXPORT_LAYOUT": "legacy-agent"},
        )
        self.assertEqual(note.path.folder, PATH_RESEARCH)

    def test_explicit_project_arg_wins_over_env(self) -> None:
        note = render_research_note(
            self._pack(),
            project="other-project",
            env={"OBSIDIAN_DEFAULT_PROJECT": "ignored-default"},
        )
        self.assertTrue(note.path.folder.startswith("10-projects/other-project/"))
        self.assertIn("project: other-project", note.content)

    def test_session_extra_project_wins_over_env_default(self) -> None:
        session = _session(extra={"project": "session-project"})
        note = render_research_note(
            self._pack(),
            session=session,
            env={"OBSIDIAN_DEFAULT_PROJECT": "env-default"},
        )
        self.assertTrue(note.path.folder.startswith("10-projects/session-project/"))
        self.assertIn("project: session-project", note.content)

    def test_env_default_project_used_when_no_explicit_or_session(self) -> None:
        note = render_research_note(
            self._pack(),
            env={"OBSIDIAN_DEFAULT_PROJECT": "env-default"},
        )
        self.assertTrue(note.path.folder.startswith("10-projects/env-default/"))
        self.assertIn("project: env-default", note.content)

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
        from yule_orchestrator.agents.obsidian.export import recommend_path

        path = recommend_path(
            title="Stripe pricing hero copy",
            kind="research",
            created_at=datetime(2026, 4, 30),
            project="yule-studio-agent",
        )
        self.assertTrue(path.folder.startswith("10-projects/yule-studio-agent/"))
        self.assertTrue(path.folder.endswith("/research"))

    def test_project_decision_uses_decisions_subdir(self) -> None:
        from yule_orchestrator.agents.obsidian.export import recommend_path

        path = recommend_path(
            title="hero copy 분할",
            kind="decision",
            created_at=datetime(2026, 4, 30),
            project="yule-studio-agent",
        )
        self.assertTrue(path.folder.endswith("/decisions"))

    def test_default_falls_back_to_default_project_when_absent(self) -> None:
        # New default: even without explicit project / session.extra, the
        # path uses the configured default project (yule-studio-agent).
        # Legacy ``Agents/Engineering/...`` is opt-in only.
        from yule_orchestrator.agents.obsidian.export import (
            DEFAULT_PROJECT,
            recommend_path,
        )

        path = recommend_path(
            title="x", kind="research", created_at=datetime(2026, 4, 30), env={}
        )
        self.assertEqual(
            path.folder, f"10-projects/{DEFAULT_PROJECT}/research"
        )

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


class ShortTitleAndFilenameTests(unittest.TestCase):
    """Regression: long Korean prompts must yield short titles and
    sanitised filenames so Obsidian/git checkouts stay readable.
    """

    LONG_PROMPT = (
        "오늘은 에이전트들이 서로 상호작용할 수 있는 구조와, 이를 코드 레벨에서 "
        "병렬 처리로 어떻게 적용할지 고민해보려 합니다. 우선은 라우팅과 합의안 "
        "공유 지점부터 정리해보고, 다음 주제로 넘어가는 식으로 진행하면 좋을 것 같아요."
    )

    def _session(self, *, prompt: str | None = None, extra: dict | None = None) -> WorkflowSession:
        return WorkflowSession(
            session_id="abc",
            prompt=prompt if prompt is not None else self.LONG_PROMPT,
            task_type="landing-page",
            state=WorkflowState.APPROVED,
            created_at=datetime(2026, 5, 5, 9, 0),
            updated_at=datetime(2026, 5, 5, 9, 5),
            extra=dict(extra or {}),
        )

    def test_long_korean_prompt_collapses_to_short_title(self) -> None:
        from yule_orchestrator.agents.obsidian.export import derive_short_title

        pack = ResearchPack(title=self.LONG_PROMPT, summary="")
        title = derive_short_title(pack, session=self._session())
        self.assertLessEqual(len(title), 50)
        # Filler prefixes are gone.
        self.assertFalse(title.startswith("오늘은 "))
        self.assertNotIn("\n", title)

    def test_session_extra_short_title_wins(self) -> None:
        from yule_orchestrator.agents.obsidian.export import derive_short_title

        pack = ResearchPack(title=self.LONG_PROMPT, summary="")
        session = self._session(extra={"short_title": "에이전트 병렬 회의 구조"})
        self.assertEqual(
            derive_short_title(pack, session=session),
            "에이전트 병렬 회의 구조",
        )

    def test_render_uses_short_title_for_h1_and_filename(self) -> None:
        pack = ResearchPack(
            title=self.LONG_PROMPT,
            summary="",
            created_at=datetime(2026, 5, 5),
        )
        session = self._session(extra={"short_title": "에이전트 병렬 회의 구조"})
        note = render_research_note(pack, session=session)
        self.assertIn("# 에이전트 병렬 회의 구조", note.content)
        # Filename basename within the 100-char cap.
        self.assertLessEqual(len(note.path.filename), 100)
        # Filename starts with date_kind- prefix.
        self.assertTrue(
            note.path.filename.startswith("2026-05-05_research-"),
            note.path.filename,
        )

    def test_filename_basename_caps_at_100_chars(self) -> None:
        # Even without an operator-curated short_title we must stay under
        # the cap on a long Korean prompt.
        pack = ResearchPack(
            title=self.LONG_PROMPT,
            summary="",
            created_at=datetime(2026, 5, 5),
        )
        note = render_research_note(pack, session=self._session())
        self.assertLessEqual(len(note.path.filename), 100)

    def test_original_prompt_preserved_in_frontmatter(self) -> None:
        pack = ResearchPack(
            title=self.LONG_PROMPT,
            summary="",
            created_at=datetime(2026, 5, 5),
        )
        session = self._session(extra={"short_title": "에이전트 병렬 회의 구조"})
        note = render_research_note(pack, session=session)
        # The full prompt body shows up in the body's "원문 요청" section
        # AND in frontmatter as ``original_prompt``.
        self.assertIn("## 원문 요청", note.content)
        self.assertIn("에이전트들이 서로 상호작용할", note.content)
        self.assertIn("original_prompt:", note.content)

    def test_clean_title_strips_research_prefix_bold_and_newlines(self) -> None:
        from yule_orchestrator.agents.obsidian.export import _clean_title

        out = _clean_title("[Research] **에이전트 회의**\n구조 정리")
        self.assertEqual(out, "에이전트 회의 구조 정리")
        self.assertNotIn("**", out)
        self.assertNotIn("[Research]", out)
        self.assertNotIn("\n", out)


if __name__ == "__main__":
    unittest.main()
