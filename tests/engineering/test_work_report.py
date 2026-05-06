"""Phase 3 — work_report deterministic builder."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.work_report import (
    WorkReport,
    build_work_report,
    format_work_report_markdown,
)


class BuildWorkReportTests(unittest.TestCase):
    def _research_extra(self) -> dict:
        return {
            "active_research_roles": ["tech-lead", "ai-engineer", "qa-engineer"],
            "role_selection_reasons": {
                "tech-lead": "always included",
                "ai-engineer": "user explicit mention",
                "qa-engineer": "user explicit mention",
            },
            "research_pack": {
                "summary": "Harness 자동화 도구 후보 4건 비교",
                "sources": [{"url": f"https://example/{i}"} for i in range(7)],
            },
            "research_synthesis": {
                "v": 1,
                "consensus": "Harness 자동화는 internal RAG로 진행",
                "open_research": ["benchmark 미실시"],
                "todos": ["[ai] eval set 정리"],
                "user_decisions_needed": ["latency budget 확정"],
            },
        }

    def test_research_only_report_has_no_code_change_flag(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt=(
                "[Research] 하네스 엔지니어링을 yule-studio-agent에 도입할 수 있을지"
            ),
            extra=self._research_extra(),
            research_stop_reason="sufficient",
        )
        self.assertFalse(report.requires_code_change)
        self.assertIsNone(report.recommended_executor_role)
        self.assertEqual(report.research_stop_reason, "sufficient")
        self.assertEqual(report.reference_count, 7)

    def test_canonical_prompt_drives_title_slug(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt=(
                "[Research] 하네스 엔지니어링을 yule-studio-agent에 도입할 수 있을지 "
                "조사해줘. 운영 흐름과 메모리 회수 정책 포함."
            ),
            extra=self._research_extra(),
        )
        # Leading [Research] tag stripped, title trimmed under 60 chars.
        self.assertNotIn("[Research]", report.title)
        self.assertLessEqual(len(report.title), 61)  # 60 + "…"
        self.assertIn("하네스", report.title)

    def test_recommendation_pulled_from_synthesis_consensus(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="harness",
            extra=self._research_extra(),
        )
        self.assertIn("RAG", report.tech_lead_recommendation)

    def test_role_decisions_carry_selection_reasons(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="harness",
            extra=self._research_extra(),
        )
        self.assertIn("tech-lead", report.role_decisions)
        self.assertEqual(
            report.role_decisions["ai-engineer"],
            "user explicit mention",
        )

    def test_under_covered_roles_surface_in_report(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="harness",
            extra=self._research_extra(),
            research_stop_reason="budget_exhausted",
            under_covered_roles=("qa-engineer",),
        )
        self.assertEqual(report.research_stop_reason, "budget_exhausted")
        self.assertEqual(report.under_covered_roles, ("qa-engineer",))

    def test_coding_proposal_flips_requires_code_change(self) -> None:
        extra = self._research_extra()
        extra["coding_proposal"] = {
            "executor_role": "backend-engineer",
            "write_scope": ["src/api/**"],
        }
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="결제 모듈 멱등성 백엔드 추가",
            extra=extra,
        )
        self.assertTrue(report.requires_code_change)
        self.assertEqual(report.recommended_executor_role, "backend-engineer")
        self.assertIn("수정 승인", report.approval_request or "")

    def test_coding_job_overrides_proposal(self) -> None:
        extra = self._research_extra()
        extra["coding_job"] = {
            "executor_role": "ai-engineer",
            "status": "ready",
        }
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="harness",
            extra=extra,
        )
        self.assertTrue(report.requires_code_change)
        self.assertEqual(report.recommended_executor_role, "ai-engineer")

    def test_user_decisions_promoted_to_next_steps(self) -> None:
        report = build_work_report(
            session_id="abc123",
            canonical_prompt="harness",
            extra=self._research_extra(),
        )
        # user_decisions_needed comes first, then todos.
        self.assertIn("latency budget 확정", report.proposed_next_steps)
        self.assertIn("[ai] eval set 정리", report.proposed_next_steps)
        self.assertEqual(
            report.proposed_next_steps[0],
            "latency budget 확정",
        )

    def test_empty_extra_yields_minimal_report(self) -> None:
        # No synthesis, no pack, no roles — the report still renders
        # with the canonical prompt as title + empty risk/next-action
        # sections.
        report = build_work_report(
            session_id=None,
            canonical_prompt="결제 멱등성 검토",
            extra={},
        )
        self.assertEqual(report.participants, ())
        self.assertEqual(report.tech_lead_recommendation, "")
        self.assertEqual(report.proposed_next_steps, ())
        self.assertFalse(report.requires_code_change)

    def test_fallback_participants_used_when_extra_silent(self) -> None:
        report = build_work_report(
            session_id="x",
            canonical_prompt="결제",
            extra={},
            fallback_participants=("tech-lead", "backend-engineer"),
        )
        self.assertEqual(
            report.participants,
            ("tech-lead", "backend-engineer"),
        )


class FormatWorkReportMarkdownTests(unittest.TestCase):
    def test_renders_full_report(self) -> None:
        report = WorkReport(
            session_id="abc123",
            title="harness RAG 도입 검토",
            canonical_prompt="[Research] 하네스 엔지니어링을 yule-studio-agent에",
            executive_summary="RAG로 진행 권고",
            research_summary="후보 4건 비교",
            tech_lead_recommendation="RAG로 진행",
            role_decisions={"ai-engineer": "RAG 도메인 전문성"},
            risks=("latency 미측정",),
            proposed_next_steps=("embedding model 확정",),
            requires_code_change=False,
            participants=("tech-lead", "ai-engineer"),
            reference_count=7,
            research_stop_reason="sufficient",
        )
        body = format_work_report_markdown(report)
        self.assertIn("업무 보고서", body)
        self.assertIn("`session abc123`", body)
        self.assertIn("**원문**", body)
        self.assertIn("**참가자**: tech-lead, ai-engineer", body)
        self.assertIn("자료 7건", body)
        self.assertIn("sufficient", body)
        self.assertIn("**Tech-lead 권고**", body)
        self.assertIn("**위험", body)
        self.assertIn("**다음 액션**", body)
        self.assertNotIn("코드 수정 필요", body)

    def test_renders_coding_approval_cta(self) -> None:
        report = WorkReport(
            session_id="abc",
            title="결제 멱등성",
            canonical_prompt="결제 멱등성 백엔드 추가",
            executive_summary="추가 권고",
            research_summary="",
            tech_lead_recommendation="추가",
            requires_code_change=True,
            recommended_executor_role="backend-engineer",
            approval_request="진행하려면 `수정 승인`",
        )
        body = format_work_report_markdown(report)
        self.assertIn("**코드 수정 필요**", body)
        self.assertIn("backend-engineer", body)
        self.assertIn("수정 승인", body)

    def test_under_covered_roles_called_out_in_meta(self) -> None:
        report = WorkReport(
            session_id="abc",
            title="t",
            canonical_prompt="harness",
            executive_summary="",
            research_summary="",
            tech_lead_recommendation="",
            participants=("tech-lead", "qa-engineer"),
            reference_count=2,
            research_stop_reason="budget_exhausted",
            under_covered_roles=("qa-engineer",),
        )
        body = format_work_report_markdown(report)
        self.assertIn("부족 role: qa-engineer", body)
        self.assertIn("budget 소진", body)


if __name__ == "__main__":
    unittest.main()
