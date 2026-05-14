"""P0-G commit 7 — 정책 stack 완전성 회귀 test.

본 test 는 P0-G 1차 (#139, parent #138) 가 land 한 8 종 정책 + 운영자
docs 의 *존재* 와 *핵심 섹션 키워드* 를 lint-style 로 검증한다. 정책이
silent regression 으로 사라지거나 핵심 표 / 섹션이 삭제되는 것을 막는다.

기존 ``test_engineering_agent_governance_doc.py`` 는 4 정책 (governance /
obsidian-governance / write-ownership / github-workflow) + 운영자 docs +
vault mirror 3 노트를 검증한다. 본 test 는 그 외 P0-G 신설 / 갱신 항목을
추가로 보호한다 — 중복 없이.

검사 대상:
  * policies/runtime/agents/engineering-agent/repo-contract-discovery.md  (P0-G 신설)
  * policies/runtime/agents/engineering-agent/growth-loop.md              (P0-G 신설)
  * policies/runtime/agents/engineering-agent/design-to-code-assets.md    (P0-G 신설)
  * policies/runtime/agents/engineering-agent/github-workflow.md §5.1     (P0-G refine — C/R/U/D)
  * policies/runtime/agents/engineering-agent/obsidian-governance.md §0    (P0-G — GitHub vs Obsidian role separation)
  * policies/runtime/agents/engineering-agent/governance.md §6.5           (P0-G — umbrella 6 layer)
  * docs/autonomy-policy.md §0                                              (P0-G — work mode/topology/scope ask-once)
  * docs/approval-matrix.md §3                                              (P0-G — vault commit/push SSoT)
"""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


_REPO_ROOT = Path(__file__).resolve().parents[2]
_POLICY_DIR = _REPO_ROOT / "policies" / "runtime" / "agents" / "engineering-agent"
_DOCS_DIR = _REPO_ROOT / "docs"


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# New policies — repo contract discovery / growth loop / design-to-code assets
# ---------------------------------------------------------------------------


class RepoContractDiscoveryDocTests(unittest.TestCase):
    """``repo-contract-discovery.md`` is the SSoT for external repo conventions."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "repo-contract-discovery.md"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        self.assertTrue(self.path.is_file(), self.path)

    def test_repo_contract_dataclass_shape_present(self) -> None:
        for needle in (
            "RepoContract",
            "issue_templates",
            "pr_templates",
            "contributing",
            "codeowners",
            "workflows",
            "branch_strategy",
            "fallback",
        ):
            self.assertIn(needle, self.text, needle)

    def test_priority_order_present(self) -> None:
        # §3 priority — ensure top 4 sources are still listed.
        for needle in (
            "ISSUE_TEMPLATE",
            "PULL_REQUEST_TEMPLATE",
            "CONTRIBUTING.md",
            "CODEOWNERS",
        ):
            self.assertIn(needle, self.text, needle)

    def test_yule_fallback_rule_present(self) -> None:
        self.assertIn("자체 규칙을 갖고 있으면 그 규칙이 우선", self.text)
        self.assertIn("fallback", self.text)

    def test_no_permission_no_fake_success(self) -> None:
        # §6 — fake success forbidden when repo access fails.
        self.assertIn("fake success 금지", self.text)


class GrowthLoopDocTests(unittest.TestCase):
    """``growth-loop.md`` defines daily / projects / resources lifecycle."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "growth-loop.md"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        self.assertTrue(self.path.is_file(), self.path)

    def test_three_buckets_present(self) -> None:
        for needle in ("resources", "projects", "daily"):
            self.assertIn(needle, self.text, needle)

    def test_promotion_flow_direction(self) -> None:
        # daily → projects → resources. The doc must reject reverse promotion.
        self.assertIn("daily", self.text)
        self.assertIn("승격", self.text)
        self.assertIn("위 방향만", self.text)

    def test_signals_for_promotion_present(self) -> None:
        # §3 — at least 2 of the 5 signals (daily 반복 / PR review 반복 /
        # postmortem root cause / repo-contract fallback / projects 같은 결정).
        signal_count = sum(
            1
            for needle in (
                "daily",
                "PR review",
                "postmortem",
                "fallback",
                "projects",
            )
            if needle in self.text
        )
        self.assertGreaterEqual(signal_count, 4, "growth loop signal coverage")


class DesignToCodeAssetDocTests(unittest.TestCase):
    """``design-to-code-assets.md`` defines designer→engineer hand-off."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "design-to-code-assets.md"
        self.text = _read(self.path)

    def test_file_exists(self) -> None:
        self.assertTrue(self.path.is_file(), self.path)

    def test_five_dimensions_present(self) -> None:
        for needle in ("의미", "형태", "컬러", "비율", "용도"):
            self.assertIn(needle, self.text, needle)

    def test_svg_source_of_truth_present(self) -> None:
        self.assertIn("SVG", self.text)
        self.assertIn("source-of-truth", self.text)

    def test_raster_boundary_present(self) -> None:
        # §4 SVG vs raster boundary
        for needle in ("그라데이션", "실사", "raster"):
            self.assertIn(needle, self.text, needle)

    def test_conflict_matrix_present(self) -> None:
        # §5 충돌 매트릭스
        self.assertIn("write-ownership", self.text)
        self.assertIn("obsidian-governance", self.text)
        self.assertIn("growth-loop", self.text)


# ---------------------------------------------------------------------------
# Updated policies — github-workflow §5.1 / obsidian-governance §0 /
# governance §6.5 / docs/autonomy-policy.md §0 / docs/approval-matrix.md §3
# ---------------------------------------------------------------------------


class GithubWorkflowSemanticSliceTests(unittest.TestCase):
    """``github-workflow.md`` §5.1 refines PR splitting to semantic CRUD-like slices."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "github-workflow.md"
        self.text = _read(self.path)

    def test_semantic_crud_classes_present(self) -> None:
        for needle in ("Create", "Read", "Update", "Delete"):
            self.assertIn(needle, self.text, needle)

    def test_exceptions_present(self) -> None:
        for needle in ("hotfix", "docs-only", "test-only", "tiny config"):
            self.assertIn(needle, self.text, needle)

    def test_five_minute_rule_present(self) -> None:
        self.assertIn("5분", self.text)
        self.assertIn("롤백 독립", self.text)

    def test_pr_size_guide_present(self) -> None:
        self.assertIn("800 줄", self.text)


class ObsidianGovernanceRoleSeparationTests(unittest.TestCase):
    """``obsidian-governance.md`` §0 — GitHub vs Obsidian role separation."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "obsidian-governance.md"
        self.text = _read(self.path)

    def test_role_separation_section_present(self) -> None:
        self.assertIn("## 0. GitHub vs Obsidian", self.text)

    def test_execution_record_vs_learning_mirror(self) -> None:
        self.assertIn("실행 기록", self.text)
        self.assertIn("학습", self.text)

    def test_bidirectional_backlink(self) -> None:
        self.assertIn("양방향", self.text)
        self.assertIn("backlink", self.text)

    def test_vault_repo_absent_fallback(self) -> None:
        # When vault repo workspace 없음 — mirror only, write 자동화는 deferred.
        self.assertIn("vault repo", self.text)
        self.assertIn("workspace", self.text)
        self.assertIn("#141", self.text)


class GovernanceUmbrellaExtensionTests(unittest.TestCase):
    """``governance.md`` umbrella now cross-links 6 layers (was 3)."""

    def setUp(self) -> None:
        self.path = _POLICY_DIR / "governance.md"
        self.text = _read(self.path)

    def test_six_layers_cross_linked(self) -> None:
        for needle in (
            "obsidian-governance.md",
            "write-ownership.md",
            "github-workflow.md",
            "repo-contract-discovery.md",
            "growth-loop.md",
            "design-to-code-assets.md",
        ):
            self.assertIn(needle, self.text, needle)

    def test_p0g_section_present(self) -> None:
        self.assertIn("P0-G", self.text)

    def test_operator_docs_cross_link(self) -> None:
        # autonomy-policy.md / approval-matrix.md cross-link.
        self.assertIn("autonomy-policy.md", self.text)
        self.assertIn("approval-matrix.md", self.text)


class AutonomyPolicyAskOnceTests(unittest.TestCase):
    """``docs/autonomy-policy.md`` §0 — work mode / topology / scope ask-once."""

    def setUp(self) -> None:
        self.path = _DOCS_DIR / "autonomy-policy.md"
        self.text = _read(self.path)

    def test_section_zero_present(self) -> None:
        self.assertIn("## 0. Work mode", self.text)

    def test_work_mode_values_present(self) -> None:
        for needle in ("autonomous_merge", "approval_required"):
            self.assertIn(needle, self.text, needle)

    def test_topology_values_present(self) -> None:
        for needle in ("single_repo", "multi_repo"):
            self.assertIn(needle, self.text, needle)

    def test_scope_values_present(self) -> None:
        for needle in (
            "single_scope",
            "full_stack_single_repo",
            "layer_scoped",
            "cross_repo_program",
        ):
            self.assertIn(needle, self.text, needle)

    def test_ask_once_contract(self) -> None:
        self.assertIn("다시 묻지 않는다", self.text)

    def test_session_memory_keys_documented(self) -> None:
        for key in (
            "work_mode",
            "topology",
            "scope",
            "mode_decided_at",
            "mode_decided_by",
        ):
            self.assertIn(key, self.text, key)


class ApprovalMatrixVaultSsotTests(unittest.TestCase):
    """``docs/approval-matrix.md`` §3 — vault commit/push 통합 SSoT."""

    def setUp(self) -> None:
        self.path = _DOCS_DIR / "approval-matrix.md"
        self.text = _read(self.path)

    def test_section_three_marker(self) -> None:
        # The P0-G refine adds "통합 commit/push 매트릭스 (P0-G 1차 SSoT)".
        self.assertIn("통합 commit/push", self.text)
        self.assertIn("SSoT", self.text)

    def test_vault_commit_l2_present(self) -> None:
        # L2 자동 commit
        self.assertIn("vault_research_log_commit", self.text)
        self.assertIn("L2", self.text)

    def test_vault_push_mode_branching(self) -> None:
        # vault_remote_push 가 mode 에 따라 L3 ↔ L2 분기.
        self.assertIn("vault_remote_push", self.text)
        self.assertIn("autonomous_merge", self.text)
        self.assertIn("approval_required", self.text)

    def test_code_vs_vault_push_separation(self) -> None:
        # §3.1 코드 repo vs vault repo 분리표.
        self.assertIn("code_push_audit", self.text)
        self.assertIn("vault_push_audit", self.text)

    def test_no_workspace_no_fake_success(self) -> None:
        # §3.2 vault repo workspace 부재 시 fake success 금지.
        self.assertIn("fake success 금지", self.text)


# ---------------------------------------------------------------------------
# Completeness — required policy doc presence check
# ---------------------------------------------------------------------------


_REQUIRED_POLICY_FILES = (
    # 4 base layers (covered by test_engineering_agent_governance_doc.py too,
    # but listed here so the P0-G stack list is auditable in one place).
    _POLICY_DIR / "governance.md",
    _POLICY_DIR / "obsidian-governance.md",
    _POLICY_DIR / "write-ownership.md",
    _POLICY_DIR / "github-workflow.md",
    # 3 P0-G new policies.
    _POLICY_DIR / "repo-contract-discovery.md",
    _POLICY_DIR / "growth-loop.md",
    _POLICY_DIR / "design-to-code-assets.md",
    # 2 operator docs (P0-G touched).
    _DOCS_DIR / "autonomy-policy.md",
    _DOCS_DIR / "approval-matrix.md",
)


class RequiredPolicyDocsExistTests(unittest.TestCase):
    """All 9 policy-stack docs must exist. Missing any = test fail."""

    def test_all_required_policy_docs_present(self) -> None:
        missing = [p for p in _REQUIRED_POLICY_FILES if not p.is_file()]
        self.assertEqual(missing, [], f"missing policy docs: {missing}")


if __name__ == "__main__":
    unittest.main()
