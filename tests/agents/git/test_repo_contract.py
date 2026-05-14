"""P0-H stage 2 commit 3 — RepoContract discovery unit tests.

Two backends + fallback are covered:

  * local clone — temp dir with the expected files, scan returns the
    discovered paths.
  * gh CLI — mocked subprocess runner with realistic API output.
  * fallback — neither backend present → ``fallback=True`` + ``failure_mode``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.git.repo_contract import (
    RepoContract,
    discover_repo_contract,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class LocalCloneBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.repo_root = self.workspace / "foo" / "bar"

    def test_full_contract_detection(self) -> None:
        _write(self.repo_root / ".github" / "ISSUE_TEMPLATE" / "feat.md", "issue")
        _write(self.repo_root / ".github" / "ISSUE_TEMPLATE" / "bug.md", "issue")
        _write(self.repo_root / ".github" / "PULL_REQUEST_TEMPLATE.md", "pr")
        _write(self.repo_root / "CONTRIBUTING.md", "## Branch strategy\n\nWe use git flow.")
        _write(self.repo_root / "README.md", "# foo/bar")
        _write(self.repo_root / "CODEOWNERS", "* @foo")
        _write(self.repo_root / ".github" / "workflows" / "ci.yml", "name: CI\non: [push]\nbranches:\n  - main")

        contract = discover_repo_contract(
            owner="foo", repo="bar", workspace_root=str(self.workspace)
        )
        self.assertEqual(contract.backend, "local_clone")
        self.assertFalse(contract.fallback)
        self.assertEqual(len(contract.issue_templates), 2)
        self.assertIn(".github/ISSUE_TEMPLATE/feat.md", contract.issue_templates)
        self.assertEqual(contract.pr_templates, (".github/PULL_REQUEST_TEMPLATE.md",))
        self.assertEqual(contract.contributing, "CONTRIBUTING.md")
        self.assertEqual(contract.readme, "README.md")
        self.assertEqual(contract.codeowners, "CODEOWNERS")
        self.assertIn(".github/workflows/ci.yml", contract.workflows)
        self.assertEqual(contract.branch_strategy, "git-flow")
        self.assertEqual(contract.primary_branch, "main")

    def test_partial_contract_only_pr_template(self) -> None:
        _write(self.repo_root / ".github" / "PULL_REQUEST_TEMPLATE.md", "pr")

        contract = discover_repo_contract(
            owner="foo", repo="bar", workspace_root=str(self.workspace)
        )
        self.assertEqual(contract.backend, "local_clone")
        self.assertFalse(contract.fallback)
        self.assertEqual(contract.pr_templates, (".github/PULL_REQUEST_TEMPLATE.md",))
        self.assertEqual(contract.issue_templates, ())
        self.assertIsNone(contract.contributing)

    def test_no_repo_workspace_returns_none_backend(self) -> None:
        # workspace_root exists but no foo/bar under it → falls to gh CLI.
        # We force gh CLI to be unavailable so we land in fallback.
        contract = discover_repo_contract(
            owner="foo",
            repo="bar",
            workspace_root=str(self.workspace),
            gh_cli_runner=_runner_raising_filenotfound,
        )
        self.assertTrue(contract.fallback)
        self.assertEqual(contract.failure_mode, "no_backend")

    def test_summary_line_for_full_contract(self) -> None:
        _write(self.repo_root / ".github" / "PULL_REQUEST_TEMPLATE.md", "pr")
        _write(self.repo_root / "CONTRIBUTING.md", "x")

        contract = discover_repo_contract(
            owner="foo", repo="bar", workspace_root=str(self.workspace)
        )
        line = contract.summary_line()
        self.assertIn("✅ foo/bar", line)
        self.assertIn("pr_templates=1", line)
        self.assertIn("contributing", line)
        self.assertIn("[local_clone]", line)


class GhCliBackendTests(unittest.TestCase):
    def _runner_factory(self, *, default_branch="main", paths=(), gh_dir=(), workflows=()):
        # Generate a callable that responds to the 4 gh api calls we make.
        def _runner(cmd, *, timeout=None):
            assert cmd[0] == "gh"
            assert cmd[1] == "api"
            target = cmd[2]
            if target == "repos/foo/bar":
                return SimpleNamespace(returncode=0, stdout=f'"{default_branch}"', stderr="")
            if target.startswith("repos/foo/bar/git/trees/"):
                return SimpleNamespace(
                    returncode=0,
                    stdout="\n".join(paths) + "\n",
                    stderr="",
                )
            if target == "repos/foo/bar/contents/.github":
                payload = [{"name": name.split("/")[-1]} for name in gh_dir]
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            if target == "repos/foo/bar/contents/.github/workflows":
                payload = [
                    {"name": name}
                    for name in workflows
                ]
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="unmocked")

        return _runner

    def test_gh_cli_detects_full_contract(self) -> None:
        runner = self._runner_factory(
            default_branch="main",
            paths=(
                "CONTRIBUTING.md",
                "README.md",
                "CODEOWNERS",
                ".github/workflows",
            ),
            gh_dir=(
                ".github/ISSUE_TEMPLATE",
                ".github/PULL_REQUEST_TEMPLATE.md",
                ".github/workflows",
            ),
            workflows=("ci.yml", "release.yaml"),
        )
        contract = discover_repo_contract(
            owner="foo", repo="bar", gh_cli_runner=runner
        )
        self.assertEqual(contract.backend, "gh_cli")
        self.assertFalse(contract.fallback)
        self.assertEqual(contract.primary_branch, "main")
        self.assertEqual(contract.contributing, "CONTRIBUTING.md")
        self.assertEqual(contract.readme, "README.md")
        self.assertEqual(contract.codeowners, "CODEOWNERS")
        self.assertEqual(contract.pr_templates, (".github/PULL_REQUEST_TEMPLATE.md",))
        self.assertEqual(
            contract.workflows,
            (".github/workflows/ci.yml", ".github/workflows/release.yaml"),
        )

    def test_gh_cli_unauthenticated_returns_fallback(self) -> None:
        def runner(cmd, *, timeout=None):
            return SimpleNamespace(returncode=4, stdout="", stderr="not authed")

        contract = discover_repo_contract(
            owner="foo", repo="bar", gh_cli_runner=runner
        )
        self.assertTrue(contract.fallback)
        self.assertEqual(contract.failure_mode, "no_backend")

    def test_gh_cli_not_installed_returns_fallback(self) -> None:
        contract = discover_repo_contract(
            owner="foo",
            repo="bar",
            gh_cli_runner=_runner_raising_filenotfound,
        )
        self.assertTrue(contract.fallback)
        self.assertEqual(contract.failure_mode, "no_backend")


class FallbackTests(unittest.TestCase):
    def test_empty_owner_returns_invalid_target(self) -> None:
        contract = discover_repo_contract(owner="", repo="bar")
        self.assertTrue(contract.fallback)
        self.assertEqual(contract.failure_mode, "invalid_target")

    def test_summary_line_for_fallback(self) -> None:
        contract = discover_repo_contract(
            owner="foo",
            repo="bar",
            gh_cli_runner=_runner_raising_filenotfound,
        )
        line = contract.summary_line()
        self.assertIn("⚠️ foo/bar", line)
        self.assertIn("fallback", line)
        self.assertIn("Yule 기본 규칙", line)


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self) -> None:
        original = RepoContract(
            owner="foo",
            repo="bar",
            primary_branch="main",
            pr_templates=(".github/PULL_REQUEST_TEMPLATE.md",),
            contributing="CONTRIBUTING.md",
            workflows=(".github/workflows/ci.yml",),
            branch_strategy="git-flow",
            backend="local_clone",
        )
        payload = original.to_dict()
        restored = RepoContract.from_dict(payload)
        self.assertEqual(restored.owner, "foo")
        self.assertEqual(restored.primary_branch, "main")
        self.assertEqual(restored.pr_templates, (".github/PULL_REQUEST_TEMPLATE.md",))
        self.assertEqual(restored.branch_strategy, "git-flow")
        self.assertEqual(restored.backend, "local_clone")

    def test_has_any_contract_property(self) -> None:
        self.assertFalse(
            RepoContract(owner="a", repo="b", fallback=True).has_any_contract
        )
        self.assertTrue(
            RepoContract(
                owner="a", repo="b", pr_templates=("X.md",)
            ).has_any_contract
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner_raising_filenotfound(cmd, *, timeout=None):  # noqa: D401
    raise FileNotFoundError("gh: command not found")


if __name__ == "__main__":
    unittest.main()
