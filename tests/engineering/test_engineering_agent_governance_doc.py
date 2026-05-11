"""Lint-style smoke test for the engineering-agent governance suite.

Issue #69 land 한 4 정책 + 운영자 docs + Obsidian mirror 3 노트가
silent regression 으로 사라지거나 핵심 섹션을 잃지 않게 막는다.
checked items 는 정책의 *contract* 자체 — 본 test 가 통과한다는 것은
governance 가 살아 있다는 뜻이다.

검사 대상:
  * `policies/runtime/agents/engineering-agent/governance.md` (umbrella)
  * `policies/runtime/agents/engineering-agent/obsidian-governance.md`
  * `policies/runtime/agents/engineering-agent/write-ownership.md`
  * `policies/runtime/agents/engineering-agent/github-workflow.md`
  * `docs/engineering-agent-governance.md`
  * `notes/vault-mirror/.../research/2026-05-08_issue-69-research-engineering-agent-governance-synthesis.md`
  * `notes/vault-mirror/.../decisions/2026-05-08_issue-69-decision-engineering-agent-authoring-policy.md`
  * `notes/vault-mirror/.../task-logs/2026-05-08_issue-69-task-log-governance-integration.md`
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIR = _REPO_ROOT / "policies" / "runtime" / "agents" / "engineering-agent"
_NOTES_DIR = (
    _REPO_ROOT
    / "notes"
    / "vault-mirror"
    / "10-projects"
    / "yule-studio-agent"
)


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Umbrella governance
# ---------------------------------------------------------------------------


class GovernanceUmbrellaTests(unittest.TestCase):
    """`governance.md` is the umbrella that cross-links the 3 layer policies."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "governance.md"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        self.assertTrue(self.path.is_file(), self.path)

    def test_four_principles_present(self) -> None:
        for needle in (
            "deterministic",
            "부서 공통",
            "graph-aware",
            "회귀 보호",
        ):
            self.assertIn(needle, self.text, needle)

    def test_three_layer_cross_links(self) -> None:
        for needle in (
            "obsidian-governance.md",
            "write-ownership.md",
            "github-workflow.md",
        ):
            self.assertIn(needle, self.text, needle)

    def test_decision_tree_q1_q2_q3_present(self) -> None:
        for needle in ("Q1", "Q2", "Q3", "gateway-mediated", "tech-lead-mediated", "role-owned"):
            self.assertIn(needle, self.text, needle)

    def test_hard_rail_six_items_present(self) -> None:
        for needle in (
            "protected branch",
            "force push",
            "auto merge",
            "production deploy",
            "secret",
            "사용자 기존 변경",
        ):
            self.assertIn(needle, self.text, needle)


# ---------------------------------------------------------------------------
# Obsidian governance
# ---------------------------------------------------------------------------


class ObsidianGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _POLICY_DIR / "obsidian-governance.md"
        self.text = _read(self.path)

    def test_naming_convention_pattern_present(self) -> None:
        # The naming convention block must declare the issue-<n>-<kind> token.
        self.assertIn("issue-<n>-<kind>-<slug>", self.text)
        for kind in ("research", "decision", "task-log", "report"):
            self.assertIn(kind, self.text, kind)

    def test_kwanlyeon_munseo_section_strict(self) -> None:
        # Must define the strict ## 관련 문서 contract + minimum 4 wikilink.
        self.assertIn("## 관련 문서", self.text)
        self.assertIn("4 wikilink", self.text)

    def test_seven_roles_listed(self) -> None:
        for role in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
            "product-designer",
        ):
            self.assertIn(role, self.text, role)


# ---------------------------------------------------------------------------
# Write ownership
# ---------------------------------------------------------------------------


class WriteOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _POLICY_DIR / "write-ownership.md"
        self.text = _read(self.path)

    def test_three_modes_defined(self) -> None:
        for mode in ("role-owned", "tech-lead-mediated", "gateway-mediated"):
            self.assertIn(mode, self.text, mode)

    def test_decision_tree_questions_present(self) -> None:
        for needle in ("[Q1]", "[Q2]", "[Q3]"):
            self.assertIn(needle, self.text, needle)

    def test_seven_role_surface_matrix(self) -> None:
        for role in (
            "tech-lead",
            "backend-engineer",
            "frontend-engineer",
            "devops-engineer",
            "qa-engineer",
            "ai-engineer",
            "product-designer",
        ):
            self.assertIn(role, self.text, role)

    def test_five_core_questions_addressed(self) -> None:
        # The 5 core questions section enumerates Q→A pairs.
        for needle in (
            "issue comment",
            "PR body",
            "Obsidian 노트",
            "tech-lead",
            "GitHub Apps",
        ):
            self.assertIn(needle, self.text, needle)


# ---------------------------------------------------------------------------
# GitHub workflow
# ---------------------------------------------------------------------------


class GitHubWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _POLICY_DIR / "github-workflow.md"
        self.text = _read(self.path)

    def test_issue_template_four_section_headers(self) -> None:
        for needle in (
            "## 어떤 기능인가요?",
            "## 작업 상세 내용",
            "## 참고할만한 자료(선택)",
            "Parent: #",
        ):
            self.assertIn(needle, self.text, needle)

    def test_pr_template_four_sections_plus_audit(self) -> None:
        for needle in (
            "## 📌 관련 이슈",
            "## ✨ 과제 내용",
            "## :camera_with_flash: 스크린샷(선택)",
            "## 📚 레퍼런스",
            "## 🤖 Agent WorkOS Audit",
        ):
            self.assertIn(needle, self.text, needle)

    def test_label_real_and_recommended_distinct(self) -> None:
        # Both tables present + recommendation rule visible.
        self.assertIn("실재 label", self.text)
        self.assertIn("추천 label", self.text)
        self.assertIn("자동 생성 금지", self.text)

    def test_progress_comment_five_sections(self) -> None:
        for needle in (
            "이번 라운드 목표",
            "변경 파일",
            "테스트 / 검증",
            "Obsidian 노트 경로",
            "다음 액션",
        ):
            self.assertIn(needle, self.text, needle)

    def test_commit_split_at_least_three(self) -> None:
        self.assertIn("최소 3 commit", self.text)
        self.assertIn("COMMIT_CONVENTION", self.text)

    def test_push_policy_hard_rails(self) -> None:
        for needle in (
            "현재 작업 브랜치",
            "force push",
            "protected branch",
            "auto merge",
            "production deploy",
        ):
            self.assertIn(needle, self.text, needle)


# ---------------------------------------------------------------------------
# Operator guide
# ---------------------------------------------------------------------------


class OperatorGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _REPO_ROOT / "docs" / "engineering-agent-governance.md"
        self.text = _read(self.path)

    def test_one_minute_decision_tree_present(self) -> None:
        for needle in ("Q1", "Q2", "Q3", "gateway-mediated", "tech-lead-mediated", "role-owned"):
            self.assertIn(needle, self.text, needle)

    def test_checklist_ten_items(self) -> None:
        # 10 checklist items numbered 1.–10.
        bullets = re.findall(r"^\[ \] \d+\.", self.text, flags=re.MULTILINE)
        self.assertGreaterEqual(
            len(bullets), 10, f"checklist found only {len(bullets)} items"
        )

    def test_policy_index_links(self) -> None:
        for needle in (
            "governance.md",
            "obsidian-governance.md",
            "write-ownership.md",
            "github-workflow.md",
            "ecc-foundation.md",
            "team-architecture-patterns.md",
        ):
            self.assertIn(needle, self.text, needle)


# ---------------------------------------------------------------------------
# Obsidian mirror notes — graph integrity
# ---------------------------------------------------------------------------


# F8 (#99) Obsidian 마이그레이션 후 — 날짜 prefix 제거 컨벤션 (`<kind>-<topic>-issue-<n>.md`).
_REQUIRED_NOTES = {
    "research": "research/research-engineering-agent-governance-synthesis-issue-69.md",
    "decision": "decisions/decision-engineering-agent-authoring-policy-issue-69.md",
    "task-log": "task-logs/task-log-governance-integration-issue-69.md",
}

_ECC_BACKLINK_NOTES = (
    "research/research-ecc-foundation.md",
    "decisions/decision-ecc-foundation.md",
    "task-logs/task-log-25-ecc.md",
)


class ObsidianMirrorTests(unittest.TestCase):
    def test_three_issue_69_notes_exist(self) -> None:
        for name, rel in _REQUIRED_NOTES.items():
            with self.subTest(name=name):
                p = _NOTES_DIR / rel
                self.assertTrue(p.is_file(), p)

    def test_each_note_has_kwanlyeon_munseo_section(self) -> None:
        for rel in _REQUIRED_NOTES.values():
            with self.subTest(rel=rel):
                text = _read(_NOTES_DIR / rel)
                self.assertIn("## 관련 문서", text)

    def test_each_note_has_minimum_four_wikilinks(self) -> None:
        for rel in _REQUIRED_NOTES.values():
            with self.subTest(rel=rel):
                text = _read(_NOTES_DIR / rel)
                links = re.findall(r"\[\[([^\]]+)\]\]", text)
                self.assertGreaterEqual(
                    len(links), 4, f"{rel} has only {len(links)} wikilinks"
                )

    def test_issue_69_notes_cross_link_each_other(self) -> None:
        # F8 마이그레이션 후 새 컨벤션 파일명 — `<kind>-<topic>-issue-<n>`.
        names = {
            "research": "research-engineering-agent-governance-synthesis-issue-69",
            "decision": "decision-engineering-agent-authoring-policy-issue-69",
            "task-log": "task-log-governance-integration-issue-69",
        }
        for kind, rel in _REQUIRED_NOTES.items():
            text = _read(_NOTES_DIR / rel)
            for other_kind, basename in names.items():
                if other_kind == kind:
                    continue
                self.assertIn(
                    f"[[{basename}]]",
                    text,
                    f"{kind} note missing cross-link to {other_kind}",
                )

    def test_ecc_mirror_notes_have_69_backlink(self) -> None:
        # #25 vault mirror notes must have been augmented with backlinks
        # to the new #69 integration notes.
        targets = (
            "research-engineering-agent-governance-synthesis-issue-69",
            "decision-engineering-agent-authoring-policy-issue-69",
            "task-log-governance-integration-issue-69",
        )
        for rel in _ECC_BACKLINK_NOTES:
            with self.subTest(rel=rel):
                text = _read(_NOTES_DIR / rel)
                for target in targets:
                    self.assertIn(f"[[{target}]]", text, f"{rel} missing [[{target}]]")


if __name__ == "__main__":
    unittest.main()
