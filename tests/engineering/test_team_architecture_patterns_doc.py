"""Smoke test for the team-architecture-patterns policy doc.

The doc is the executor's deliverable for issue #48 — a single source
of truth that names the 6 Harness patterns, the Yule mapping, the
tech-lead single-write-subject model, the orchestration contract
(routing matrix / review gate / approval gate), and the next actions.

If the doc disappears or loses one of its mandatory sections silently,
the runtime won't break — but a downstream PR will quietly remove the
contract this issue committed to. This smoke test holds the
contract: keep the file, keep the sections.
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_PATH = (
    _REPO_ROOT
    / "policies"
    / "runtime"
    / "agents"
    / "engineering-agent"
    / "team-architecture-patterns.md"
)


_REQUIRED_SECTIONS = (
    "## 1. 목표",
    "## 2. 현재 Yule 기준선",
    "## 3. Harness 6 패턴 — 매핑 + 도입 결정",
    "### 3.1 Yule gateway 기본 패턴 조합",
    "## 4. Gateway 책임 범위 (재정의)",
    "## 5. 역할별 책임 — 실행 주체 vs 분석 관점 분리 기준",
    "## 6. Orchestration Contract — Routing Matrix / Review Gate / Approval Gate",
    "### 6.1 Routing Matrix",
    "### 6.2 Review Gate",
    "### 6.3 Approval Gate",
    "## 7. Progressive Disclosure Skill 구조",
    "## 8. Harness Evolution — Feedback Loop",
    "## 9. SPOF / 리스크",
    "## 10. Self-check / 검증",
    "## 11. 다음 액션",
)


_REQUIRED_PATTERN_KEYWORDS = (
    "Pipeline",
    "Fan-out",
    "Expert Pool",
    "Producer-Reviewer",
    "Supervisor",
    "Hierarchical Delegation",
)


class TeamArchitecturePatternsDocTests(unittest.TestCase):
    """The doc is the contract. Pin its presence and shape."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _DOC_PATH.read_text(encoding="utf-8")

    def test_doc_file_exists(self) -> None:
        self.assertTrue(
            _DOC_PATH.is_file(),
            msg=f"missing policy doc: {_DOC_PATH}",
        )

    def test_required_sections_present(self) -> None:
        for marker in _REQUIRED_SECTIONS:
            self.assertIn(
                marker,
                self.text,
                msg=f"required section missing: {marker!r}",
            )

    def test_six_harness_patterns_named(self) -> None:
        for keyword in _REQUIRED_PATTERN_KEYWORDS:
            self.assertIn(
                keyword,
                self.text,
                msg=f"Harness pattern keyword missing: {keyword!r}",
            )

    def test_tech_lead_is_single_write_subject(self) -> None:
        # The doc must explicitly state tech-lead as the only write subject.
        # Two anchor sentences checked because the rule shows up in
        # both the role-split section and the routing matrix.
        self.assertIn("단일 write 주체", self.text)
        self.assertIn("write subject", self.text)

    def test_protected_branches_and_l4_actions_called_out(self) -> None:
        # The contract must remind the reader that prod / main / merge /
        # deploy are out of scope for the agent — pin the strings so a
        # future drift loses the test, not the safety rail.
        self.assertIn("main / master / prod / release", self.text)
        self.assertIn("production deploy", self.text)
        self.assertIn("force push", self.text)


if __name__ == "__main__":
    unittest.main()
