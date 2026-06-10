"""RepoContract — tag/version policy 감지 회귀.

contract:
  - changelog 만 있으면 ``tag_policy == 'changelog_driven'``.
  - package.json/pyproject.toml 만 있으면 ``'version_file_only'``.
  - .github/workflows/release.yml 이 있으면 ``'workflow_driven'`` (가장
    강한 신호).
  - 아무 신호도 없으면 ``'none'`` + ``has_tag_policy == False``.
  - dict round-trip 으로 새 필드 보존.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.git.repo_contract import (
    RepoContract,
    derive_tag_policy,
    discover_repo_contract,
)


class DeriveTagPolicyTests(unittest.TestCase):
    def test_no_signals_returns_none(self) -> None:
        self.assertEqual(
            derive_tag_policy(changelog=None, version_files=(), release_workflows=()),
            "none",
        )

    def test_workflow_wins_when_multiple_signals(self) -> None:
        self.assertEqual(
            derive_tag_policy(
                changelog="CHANGELOG.md",
                version_files=("package.json",),
                release_workflows=(".github/workflows/release.yml",),
            ),
            "workflow_driven",
        )

    def test_changelog_when_no_workflow(self) -> None:
        self.assertEqual(
            derive_tag_policy(
                changelog="CHANGELOG.md",
                version_files=(),
                release_workflows=(),
            ),
            "changelog_driven",
        )

    def test_version_file_only_when_others_missing(self) -> None:
        self.assertEqual(
            derive_tag_policy(
                changelog=None,
                version_files=("pyproject.toml",),
                release_workflows=(),
            ),
            "version_file_only",
        )


class LocalCloneTagSignalTests(unittest.TestCase):
    def _seed(self, **files: str) -> Path:
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: None)  # tmpdir is best-effort
        repo = Path(tmp) / "owner" / "repo"
        repo.mkdir(parents=True)
        for path, content in files.items():
            full = repo / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        return Path(tmp)

    def test_changelog_and_version_file_detected(self) -> None:
        ws = self._seed(
            **{
                "package.json": '{"version":"1.0.0"}',
                "CHANGELOG.md": "## 1.0.0\n- init",
                "README.md": "# repo",
            }
        )
        c = discover_repo_contract(
            owner="owner", repo="repo", workspace_root=str(ws)
        )
        self.assertEqual(c.changelog, "CHANGELOG.md")
        self.assertEqual(c.version_files, ("package.json",))
        self.assertEqual(c.tag_policy, "changelog_driven")
        self.assertTrue(c.has_tag_policy)
        # ssot_paths 도 새 신호를 포함
        self.assertIn("CHANGELOG.md", c.ssot_paths)
        self.assertIn("package.json", c.ssot_paths)

    def test_release_workflow_overrides_to_workflow_driven(self) -> None:
        ws = self._seed(
            **{
                "package.json": '{"version":"1.0.0"}',
                "CHANGELOG.md": "## 1.0.0",
                ".github/workflows/release.yml": "name: release",
                ".github/workflows/ci.yml": "name: ci",
            }
        )
        c = discover_repo_contract(
            owner="owner", repo="repo", workspace_root=str(ws)
        )
        self.assertEqual(c.tag_policy, "workflow_driven")
        # release.yml 만 release_workflows 에 들어가고 ci.yml 은 제외
        self.assertEqual(c.release_workflows, (".github/workflows/release.yml",))
        # 전체 workflows 에는 두 개 다 있어야 함 (회귀 가드)
        self.assertEqual(
            c.workflows,
            (".github/workflows/ci.yml", ".github/workflows/release.yml"),
        )

    def test_publish_workflow_token_also_counts(self) -> None:
        ws = self._seed(
            **{
                ".github/workflows/publish.yml": "name: publish",
            }
        )
        c = discover_repo_contract(
            owner="owner", repo="repo", workspace_root=str(ws)
        )
        self.assertEqual(c.tag_policy, "workflow_driven")
        self.assertIn(".github/workflows/publish.yml", c.release_workflows)

    def test_no_tag_signals_returns_none(self) -> None:
        ws = self._seed(**{"README.md": "# repo"})
        c = discover_repo_contract(
            owner="owner", repo="repo", workspace_root=str(ws)
        )
        self.assertEqual(c.tag_policy, "none")
        self.assertFalse(c.has_tag_policy)


class RoundTripTests(unittest.TestCase):
    def test_dict_round_trip_preserves_tag_fields(self) -> None:
        original = RepoContract(
            owner="o",
            repo="r",
            changelog="CHANGELOG.md",
            version_files=("package.json", "pyproject.toml"),
            release_workflows=(".github/workflows/release.yml",),
            tag_policy="workflow_driven",
        )
        restored = RepoContract.from_dict(original.to_dict())
        self.assertEqual(restored.changelog, "CHANGELOG.md")
        self.assertEqual(restored.version_files, ("package.json", "pyproject.toml"))
        self.assertEqual(restored.release_workflows, (".github/workflows/release.yml",))
        self.assertEqual(restored.tag_policy, "workflow_driven")
        self.assertTrue(restored.has_tag_policy)


if __name__ == "__main__":
    unittest.main()
