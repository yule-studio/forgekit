"""P0-H stage 2 commit 6 — status surface 신규 필드 회귀.

Covers ``SessionStatusReport`` extension + ``format_status_diagnostic_response``
new lines:

  * repo / mode / topology / scope
  * branch / PR number
  * repo contract detected + summary
  * Obsidian mirror path

When the gateway has populated ``session.extra`` with these keys, the
status surface shows them. When absent, lines are silently skipped
so old-shape sessions render the way they always did.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any


try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.session_status import (
    diagnose_session,
)
from yule_orchestrator.discord.engineering_conversation import (
    format_status_diagnostic_response,
)


def _session(
    *,
    session_id: str = "sess-p0h-1",
    extra=None,
    role_sequence=(),
    write_requested: bool = False,
):
    return SimpleNamespace(
        session_id=session_id,
        state=SimpleNamespace(value="needs_research"),
        task_type="research",
        prompt="P0-H stage 2 작업",
        extra=dict(extra or {}),
        role_sequence=tuple(role_sequence),
        write_requested=write_requested,
        write_blocked_reason=None,
        progress_notes=(),
    )


# ---------------------------------------------------------------------------
# diagnose_session — new fields surface from session.extra
# ---------------------------------------------------------------------------


class DiagnoseSessionRepoFieldsTests(unittest.TestCase):
    def test_repo_field_extracted_from_github_target(self) -> None:
        sess = _session(
            extra={
                "github_target": {
                    "kind": "pull_request",
                    "owner": "yule-studio",
                    "repo": "yule-studio-agent",
                    "number": 142,
                }
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(report.repository, "yule-studio/yule-studio-agent")
        self.assertEqual(report.pull_request_number, 142)

    def test_branch_field_extracted_from_tree_target(self) -> None:
        sess = _session(
            extra={
                "github_target": {
                    "kind": "tree",
                    "owner": "yule-studio",
                    "repo": "yule-studio-agent",
                    "branch_or_sha": "feature/p0h",
                }
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(report.branch_name, "feature/p0h")

    def test_explicit_branch_name_overrides_target_derive(self) -> None:
        sess = _session(
            extra={
                "github_target": {
                    "kind": "tree",
                    "owner": "foo",
                    "repo": "bar",
                    "branch_or_sha": "main",
                },
                "branch_name": "feature/explicit",
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(report.branch_name, "feature/explicit")

    def test_mode_topology_scope_round_through(self) -> None:
        sess = _session(
            extra={
                "work_mode": "autonomous_merge",
                "topology": "multi_repo",
                "scope": "cross_repo_program",
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(report.work_mode, "autonomous_merge")
        self.assertEqual(report.topology, "multi_repo")
        self.assertEqual(report.scope_mode, "cross_repo_program")


class DiagnoseSessionRepoContractTests(unittest.TestCase):
    def test_detected_contract_sets_flag_true(self) -> None:
        sess = _session(
            extra={
                "repo_contract": {
                    "owner": "foo",
                    "repo": "bar",
                    "pr_templates": [".github/PULL_REQUEST_TEMPLATE.md"],
                    "fallback": False,
                    "backend": "local_clone",
                }
            }
        )
        report = diagnose_session(sess)
        self.assertTrue(report.repo_contract_detected)

    def test_fallback_contract_sets_flag_false(self) -> None:
        sess = _session(
            extra={
                "repo_contract": {
                    "owner": "foo",
                    "repo": "bar",
                    "fallback": True,
                    "failure_mode": "no_backend",
                }
            }
        )
        report = diagnose_session(sess)
        self.assertFalse(report.repo_contract_detected)

    def test_repo_contract_summary_from_extra_key(self) -> None:
        sess = _session(
            extra={
                "repo_contract": {"owner": "foo", "repo": "bar"},
                "repo_contract_summary": "✅ foo/bar — pr_templates=1",
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(
            report.repo_contract_summary, "✅ foo/bar — pr_templates=1"
        )

    def test_obsidian_mirror_path_round_through(self) -> None:
        sess = _session(
            extra={
                "obsidian_mirror_path": "notes/vault-mirror/10-projects/x/task-log.md",
            }
        )
        report = diagnose_session(sess)
        self.assertEqual(
            report.obsidian_mirror_path,
            "notes/vault-mirror/10-projects/x/task-log.md",
        )


# ---------------------------------------------------------------------------
# format_status_diagnostic_response — new lines rendered when fields set
# ---------------------------------------------------------------------------


class FormatStatusResponseLinesTests(unittest.TestCase):
    def test_no_extra_fields_no_new_lines(self) -> None:
        # Old-shape session — only standard fields present.
        sess = _session(extra={"research_pack": {"items": []}})
        body = format_status_diagnostic_response(sess)
        # Standard lines still present.
        self.assertIn("세션:", body)
        self.assertIn("research_pack:", body)
        # No new P0-H lines.
        self.assertNotIn("- repo:", body)
        self.assertNotIn("- mode:", body)
        self.assertNotIn("- topology:", body)
        self.assertNotIn("- repo contract", body)
        self.assertNotIn("- Obsidian mirror", body)

    def test_full_p0h_fields_all_rendered(self) -> None:
        sess = _session(
            extra={
                "github_target": {
                    "kind": "pull_request",
                    "owner": "yule-studio",
                    "repo": "yule-studio-agent",
                    "number": 142,
                },
                "work_mode": "autonomous_merge",
                "topology": "single_repo",
                "scope": "single_scope",
                "branch_name": "feature/p0h",
                "repo_contract": {
                    "owner": "yule-studio",
                    "repo": "yule-studio-agent",
                    "fallback": False,
                    "backend": "local_clone",
                },
                "repo_contract_summary": "✅ yule-studio/yule-studio-agent — pr_templates=1 [local_clone]",
                "obsidian_mirror_path": "notes/vault-mirror/10-projects/x.md",
            }
        )
        body = format_status_diagnostic_response(sess)
        self.assertIn("- repo: `yule-studio/yule-studio-agent`", body)
        self.assertIn("- mode: `autonomous_merge`", body)
        self.assertIn("- topology: `single_repo`", body)
        self.assertIn("- scope: `single_scope`", body)
        self.assertIn("- branch: `feature/p0h`", body)
        self.assertIn("- PR: #142", body)
        self.assertIn("- repo contract: ✅ yule-studio/yule-studio-agent", body)
        self.assertIn(
            "- Obsidian mirror: `notes/vault-mirror/10-projects/x.md`", body
        )

    def test_partial_fields_only_render_what_is_set(self) -> None:
        sess = _session(
            extra={
                "github_target": {
                    "kind": "repo",
                    "owner": "foo",
                    "repo": "bar",
                },
                "work_mode": "approval_required",
            }
        )
        body = format_status_diagnostic_response(sess)
        self.assertIn("- repo: `foo/bar`", body)
        self.assertIn("- mode: `approval_required`", body)
        # No topology / scope set.
        self.assertNotIn("- topology:", body)
        self.assertNotIn("- scope:", body)
        # No PR — repo-root kind doesn't emit branch or PR.
        self.assertNotIn("- PR:", body)
        # No repo_contract — line skipped.
        self.assertNotIn("- repo contract", body)

    def test_fallback_contract_renders_without_summary(self) -> None:
        sess = _session(
            extra={
                "repo_contract": {
                    "owner": "foo",
                    "repo": "bar",
                    "fallback": True,
                    "failure_mode": "no_backend",
                }
            }
        )
        body = format_status_diagnostic_response(sess)
        # No precomputed summary → falls back to "예/아니오" line.
        self.assertIn("repo contract detected: 아니오", body)


if __name__ == "__main__":
    unittest.main()
