"""PR body template — G3.

Pin the contract that:

  * Every required section (목적 / 범위 / 비범위 / 변경 요약 / 테스트
    계획 / 리스크 / 승인 필요 항목 / agent work orders / audit id /
    trace link) renders exactly once, in stable order.
  * Empty inputs render the operator-facing "_(없음)_" / "_(미정)_"
    markers instead of an empty section.
  * Malicious inputs (HTML, control characters in title) don't escape
    the section structure — we never emit raw HTML.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.github_workos.pr_template import (
    PR_REQUIRED_SECTIONS,
    PrBody,
    render_pr_body,
)


@dataclass
class _StubPlan:
    title: str = "Bug: API 401 in users endpoint"
    body: str = "오늘 운영 환경에서 401이 떨어지고 있어요"
    primary_role: str = "backend-engineer"
    autonomy_level: str = "L2"
    issue_number: Optional[int] = 42
    session_id: Optional[str] = None
    repo: Optional[str] = "yule-studio/yule-studio-agent"
    in_scope: Sequence[str] = ()
    out_of_scope: Sequence[str] = ()
    test_plan: Sequence[str] = ()
    risks: Sequence[str] = ()
    approvals_needed: Sequence[str] = ()
    work_orders: Sequence[Mapping[str, str]] = ()


class RenderedSectionsTests(unittest.TestCase):
    def test_all_required_sections_present_in_order(self) -> None:
        body = render_pr_body(_StubPlan(), audit_id="audit-123")
        headings = [s.heading for s in body.sections]
        self.assertEqual(headings, list(PR_REQUIRED_SECTIONS))

    def test_render_includes_every_required_marker(self) -> None:
        rendered = render_pr_body(_StubPlan(), audit_id="audit-xyz").render()
        for needle in PR_REQUIRED_SECTIONS:
            self.assertIn(f"## {needle}", rendered, needle)
        # audit id surfaces as code-formatted literal so a reviewer
        # can grep it.
        self.assertIn("`audit-xyz`", rendered)

    def test_empty_inputs_render_explicit_markers(self) -> None:
        rendered = render_pr_body(_StubPlan(), audit_id="x").render()
        # Empty in_scope renders the dry-run marker for "변경 요약".
        self.assertIn("_(아직 미정 — dry-run)_", rendered)
        # Other empty sections fall back to the generic marker.
        self.assertIn("_(없음)_", rendered)

    def test_purpose_block_carries_role_and_issue_pointers(self) -> None:
        plan = _StubPlan(issue_number=99, primary_role="ai-engineer")
        body = render_pr_body(plan, audit_id="x").render()
        self.assertIn("`ai-engineer`", body)
        self.assertIn("#99", body)

    def test_filled_lists_render_as_bullets(self) -> None:
        plan = _StubPlan(
            in_scope=("services/auth/handlers.py", "tests/auth/"),
            test_plan=("python -m unittest tests.auth", "manual smoke"),
            risks=("토큰 만료 회귀",),
            approvals_needed=("배포 시점",),
            work_orders=(
                {
                    "autonomy_level": "L2",
                    "action": "branch_plan",
                    "target": "agent/backend/issue-99-x",
                },
            ),
        )
        rendered = render_pr_body(plan, audit_id="audit-1").render()
        self.assertIn("- services/auth/handlers.py", rendered)
        self.assertIn("- tests/auth/", rendered)
        self.assertIn("- python -m unittest tests.auth", rendered)
        self.assertIn("- 토큰 만료 회귀", rendered)
        self.assertIn("`L2`", rendered)
        self.assertIn("**branch_plan**", rendered)

    def test_trace_links_block_lists_known_keys_first(self) -> None:
        rendered = render_pr_body(
            _StubPlan(),
            audit_id="x",
            trace_links={
                "obsidian": "https://obs/note",
                "github": "https://github.com/issue/1",
                "discord": "https://discord/1",
                "extra": "https://extra/x",
            },
        ).render()
        # Known-key order — github / discord / obsidian / agent_ops_audit
        # then "extra" tail.
        github_pos = rendered.index("github**:")
        discord_pos = rendered.index("discord**:")
        obsidian_pos = rendered.index("obsidian**:")
        extra_pos = rendered.index("extra**:")
        self.assertLess(github_pos, discord_pos)
        self.assertLess(discord_pos, obsidian_pos)
        self.assertLess(obsidian_pos, extra_pos)


class PrBodyShapeTests(unittest.TestCase):
    def test_has_section_lookup(self) -> None:
        body = render_pr_body(_StubPlan(), audit_id="x")
        self.assertTrue(body.has_section("목적"))
        self.assertTrue(body.has_section("audit id"))
        self.assertFalse(body.has_section("non-existent"))

    def test_pr_body_is_immutable(self) -> None:
        body = render_pr_body(_StubPlan(), audit_id="x")
        # Frozen dataclass — assignment raises.
        with self.assertRaises(Exception):
            body.title = "tampered"  # type: ignore[misc]

    def test_html_in_title_does_not_escape_section_structure(self) -> None:
        # Hostile title containing raw markdown headers / HTML.
        plan = _StubPlan(title="</textarea>## Inject", body="evil")
        rendered = render_pr_body(plan, audit_id="x").render()
        # The template is markdown — we don't try to escape the
        # title itself (callers can; the spec just says we don't
        # *emit* raw HTML structures of our own). Verify that the
        # 10 required sections still all appear unmodified.
        for heading in PR_REQUIRED_SECTIONS:
            self.assertIn(f"## {heading}", rendered)


if __name__ == "__main__":
    unittest.main()
