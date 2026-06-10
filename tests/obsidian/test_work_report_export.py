"""Phase 5 — Obsidian work-report kind + render."""

from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.obsidian.export import (
    PROJECT_WORK_REPORTS_SUBDIR,
    recommend_path,
    render_work_report_note,
)
from yule_engineering.agents.reports.work_report import WorkReport


class WorkReportPathRoutingTests(unittest.TestCase):
    def test_work_report_kind_routes_to_reports_subdir(self) -> None:
        path = recommend_path(
            title="harness 도입 검토",
            kind="work-report",
            created_at=datetime(2026, 5, 6),
            project="yule-studio-agent",
        )
        self.assertEqual(
            path.folder,
            f"10-projects/yule-studio-agent/{PROJECT_WORK_REPORTS_SUBDIR}",
        )
        self.assertTrue(path.filename.startswith("work-report-"))

    def test_work_report_alias_underscore_kind_also_routes(self) -> None:
        path = recommend_path(
            title="harness",
            kind="work_report",
            created_at=datetime(2026, 5, 6),
            project="yule-studio-agent",
        )
        self.assertEqual(
            path.folder,
            f"10-projects/yule-studio-agent/{PROJECT_WORK_REPORTS_SUBDIR}",
        )

    def test_work_report_filename_slug_capped(self) -> None:
        long_title = "하" * 200
        path = recommend_path(
            title=long_title,
            kind="work-report",
            created_at=datetime(2026, 5, 6),
            project="yule-studio-agent",
        )
        self.assertLessEqual(len(path.filename), 100)


class RenderWorkReportNoteTests(unittest.TestCase):
    def _report(self, **overrides) -> WorkReport:
        defaults = dict(
            session_id="abc12345",
            title="harness RAG 도입 검토",
            canonical_prompt=(
                "[Research] 하네스 엔지니어링을 yule-studio-agent에 도입할 수 있을지"
            ),
            executive_summary="RAG로 진행 권고",
            research_summary="후보 4건 비교",
            tech_lead_recommendation="RAG로 진행",
            role_decisions={"ai-engineer": "RAG 도메인 전문성"},
            risks=("latency 미측정",),
            proposed_next_steps=("embedding model 확정",),
            requires_code_change=False,
            participants=("tech-lead", "ai-engineer", "qa-engineer"),
            reference_count=7,
            research_stop_reason="sufficient",
        )
        defaults.update(overrides)
        return WorkReport(**defaults)

    def test_renders_frontmatter_and_body_under_reports_subdir(self) -> None:
        note = render_work_report_note(
            report=self._report(),
            project="yule-studio-agent",
            exported_at=datetime(2026, 5, 6),
        )
        self.assertIn(
            f"10-projects/yule-studio-agent/{PROJECT_WORK_REPORTS_SUBDIR}",
            note.path.folder,
        )
        self.assertTrue(note.path.filename.endswith(".md"))
        # Frontmatter carries kind + participants + project
        self.assertEqual(note.frontmatter["kind"], "work-report")
        self.assertEqual(note.frontmatter["session_id"], "abc12345")
        self.assertEqual(note.frontmatter["project"], "yule-studio-agent")
        self.assertEqual(note.frontmatter["reference_count"], 7)
        self.assertIn("ai-engineer", note.frontmatter["participants"])
        # Body sections
        self.assertIn("# harness RAG 도입 검토", note.content)
        self.assertIn("## 원문", note.content)
        self.assertIn("[Research]", note.content)
        self.assertIn("## 요약", note.content)
        self.assertIn("## Tech-lead 권고", note.content)
        self.assertIn("## 참가자", note.content)
        self.assertIn("## 역할별 참여 사유", note.content)
        self.assertIn("## 위험", note.content)
        self.assertIn("## 다음 액션", note.content)
        self.assertNotIn("코드 수정 권한", note.content)

    def test_renders_coding_cta_when_requires_change(self) -> None:
        note = render_work_report_note(
            report=self._report(
                requires_code_change=True,
                recommended_executor_role="backend-engineer",
                approval_request="진행하려면 `수정 승인`이라고 답해 주세요.",
            ),
            project="yule-studio-agent",
            exported_at=datetime(2026, 5, 6),
        )
        self.assertIn("코드 수정 권한", note.content)
        self.assertIn("backend-engineer", note.content)
        self.assertIn("수정 승인", note.content)
        self.assertTrue(note.frontmatter["requires_code_change"])
        self.assertEqual(
            note.frontmatter["recommended_executor_role"],
            "backend-engineer",
        )

    def test_dict_payload_is_accepted(self) -> None:
        # The router persists the work_report as a plain dict on
        # session.extra; the renderer must accept that shape directly.
        payload = {
            "session_id": "abc",
            "title": "harness",
            "canonical_prompt": "[Research] harness",
            "executive_summary": "RAG",
            "research_summary": "",
            "tech_lead_recommendation": "RAG",
            "role_decisions": {"ai-engineer": "domain"},
            "risks": ["latency"],
            "proposed_next_steps": ["embedding"],
            "requires_code_change": False,
            "participants": ["tech-lead", "ai-engineer"],
            "reference_count": 5,
        }
        note = render_work_report_note(
            report=payload,
            project="yule-studio-agent",
            exported_at=datetime(2026, 5, 6),
        )
        self.assertEqual(note.frontmatter["session_id"], "abc")
        self.assertIn("# harness", note.content)
        self.assertIn("ai-engineer", note.content)

    def test_long_title_trimmed_in_frontmatter(self) -> None:
        report = self._report(title="가" * 200)
        note = render_work_report_note(
            report=report,
            project="yule-studio-agent",
            exported_at=datetime(2026, 5, 6),
        )
        # Frontmatter title trimmed to TITLE_LIMIT (50).
        title_value = note.frontmatter["title"]
        self.assertLessEqual(len(title_value), 51)  # 50 + …

    def test_under_covered_roles_surface_in_meta_section(self) -> None:
        report = self._report(
            research_stop_reason="budget_exhausted",
            under_covered_roles=("qa-engineer",),
        )
        note = render_work_report_note(
            report=report,
            project="yule-studio-agent",
            exported_at=datetime(2026, 5, 6),
        )
        self.assertIn("## research 메타", note.content)
        self.assertIn("budget_exhausted", note.content)
        self.assertIn("under_covered_roles: qa-engineer", note.content)


if __name__ == "__main__":
    unittest.main()
