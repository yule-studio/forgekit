from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.obsidian_git import (
    ObsidianGitError,
    commit_single_file,
    find_git_repo_root,
    is_git_repo,
)


def _git_available() -> bool:
    return shutil.which("git") is not None


def _init_repo(repo_root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        ["git", "config", "user.email", "obsidian-sync@example.com"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        ["git", "config", "user.name", "obsidian-sync test"],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        check=True,
        cwd=repo_root,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        text=True,
        capture_output=True,
    )


@unittest.skipUnless(_git_available(), "git executable not on PATH")
class FindRepoRootTestCase(unittest.TestCase):
    def test_returns_none_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_git_repo_root(Path(tmp)))
            self.assertFalse(is_git_repo(Path(tmp)))

    def test_finds_repo_root_from_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            sub = root / "Agents" / "Engineering" / "Research"
            sub.mkdir(parents=True)
            self.assertEqual(find_git_repo_root(sub), root.resolve())
            self.assertTrue(is_git_repo(sub))


@unittest.skipUnless(_git_available(), "git executable not on PATH")
class CommitSingleFileTestCase(unittest.TestCase):
    def test_commits_only_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            target = root / "Agents/Engineering/Research/note.md"
            _write(target, "# note\n")
            unrelated = root / "untracked.md"
            _write(unrelated, "noise\n")

            result = commit_single_file(
                root, target, message="obsidian sync: abc note.md"
            )
            self.assertTrue(result.committed)
            self.assertIsNotNone(result.commit_sha)
            self.assertFalse(result.dry_run)

            # Only target.md is in the commit
            tree = _git(root, "show", "--name-only", "--pretty=", "HEAD").stdout
            self.assertIn("Agents/Engineering/Research/note.md", tree)
            self.assertNotIn("untracked.md", tree)

            # Unrelated file is still untracked, not staged
            status = _git(root, "status", "--porcelain").stdout
            self.assertIn("?? untracked.md", status)

    def test_dry_run_does_not_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            target = root / "note.md"
            _write(target, "# note\n")

            result = commit_single_file(
                root, target, message="x", dry_run=True
            )
            self.assertFalse(result.committed)
            self.assertTrue(result.dry_run)
            self.assertIsNone(result.commit_sha)

            # No commits exist yet
            log = subprocess.run(
                ["git", "-C", str(root), "log", "--oneline"],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(log.stdout.strip(), "")

    def test_no_changes_when_file_already_at_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            target = root / "note.md"
            _write(target, "# note\n")

            first = commit_single_file(root, target, message="first commit")
            self.assertTrue(first.committed)

            # Same content, second commit attempt
            second = commit_single_file(
                root, target, message="second commit"
            )
            self.assertFalse(second.committed)
            self.assertTrue(second.no_changes)
            self.assertIsNone(second.commit_sha)

    def test_refuses_when_staged_changes_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            target = root / "note.md"
            _write(target, "# note\n")
            other = root / "other.md"
            _write(other, "# other\n")
            _git(root, "add", "--", str(other))

            with self.assertRaises(ObsidianGitError) as ctx:
                commit_single_file(
                    root, target, message="x"
                )
            self.assertIn("staged changes", str(ctx.exception))

            # other.md remains staged, target.md remains untracked
            status = _git(root, "status", "--porcelain").stdout
            self.assertIn("A  other.md", status)

    def test_refuses_target_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "vault"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            _init_repo(root)

            external = outside / "leak.md"
            _write(external, "# leak\n")

            with self.assertRaises(ObsidianGitError) as ctx:
                commit_single_file(root, external, message="x")
            self.assertIn("outside the vault repo", str(ctx.exception))

    def test_empty_message_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            target = root / "note.md"
            _write(target, "# note\n")

            with self.assertRaises(ObsidianGitError):
                commit_single_file(root, target, message="   ")


if __name__ == "__main__":
    unittest.main()
