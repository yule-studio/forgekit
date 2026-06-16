"""Repo-local git write safety hard rail (home-git-accident prevention).

Pins: HOME / ambiguous / non-repo write targets are refused, broad
`git add .` / `-A` / `--all` is refused, all writes use `git -C`, and the
forbidden patterns are not re-introduced into automation code.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.governance.git_path_safety import (
    BroadStageError,
    UnsafeGitPathError,
    assert_not_broad_stage,
    assert_safe_git_repo_path,
    run_safe_git,
    safe_git_argv,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


class SafePathTests(unittest.TestCase):
    def test_accepts_real_repo(self) -> None:
        self.assertEqual(assert_safe_git_repo_path(_REPO_ROOT), _REPO_ROOT.resolve())

    def test_rejects_home(self) -> None:
        with self.assertRaises(UnsafeGitPathError):
            assert_safe_git_repo_path(os.path.expanduser("~"))

    def test_rejects_tilde_and_dot_and_empty(self) -> None:
        for bad in ("~", ".", "", "  ", "./"):
            with self.assertRaises(UnsafeGitPathError):
                assert_safe_git_repo_path(bad)

    def test_rejects_relative(self) -> None:
        with self.assertRaises(UnsafeGitPathError):
            assert_safe_git_repo_path("some/relative/path")

    def test_rejects_ancestor_of_home(self) -> None:
        # the filesystem root is an ancestor of HOME → too broad to write at
        home_parents = Path(os.path.expanduser("~")).resolve().parents
        root = home_parents[len(home_parents) - 1]  # filesystem root
        with self.assertRaises(UnsafeGitPathError):
            assert_safe_git_repo_path(str(root))

    def test_rejects_nonexistent(self) -> None:
        with self.assertRaises(UnsafeGitPathError):
            assert_safe_git_repo_path("/nonexistent/xyz/repo")

    def test_rejects_non_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnsafeGitPathError):
                assert_safe_git_repo_path(tmp)  # no .git

    def test_accepts_temp_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(Path(tmp))
            self.assertEqual(assert_safe_git_repo_path(tmp), Path(tmp).resolve())


class BroadStageTests(unittest.TestCase):
    def test_rejects_add_dot_dash_a_all(self) -> None:
        for bad in (["add", "."], ["add", "-A"], ["add", "--all"], ["add", ":/"]):
            with self.assertRaises(BroadStageError):
                assert_not_broad_stage(bad)

    def test_rejects_commit_all(self) -> None:
        for bad in (["commit", "-a", "-m", "x"], ["commit", "--all"]):
            with self.assertRaises(BroadStageError):
                assert_not_broad_stage(bad)

    def test_allows_scoped(self) -> None:
        assert_not_broad_stage(["add", "--", "notes/vault-mirror"])
        assert_not_broad_stage(["commit", "-m", "x", "--", "notes/vault-mirror"])
        assert_not_broad_stage(["status", "--porcelain"])


class SafeArgvTests(unittest.TestCase):
    def test_builds_git_dash_c(self) -> None:
        argv = safe_git_argv(_REPO_ROOT, ["status", "--porcelain"])
        self.assertEqual(argv[:3], ["git", "-C", str(_REPO_ROOT.resolve())])

    def test_run_safe_git_works(self) -> None:
        out = run_safe_git(_REPO_ROOT, ["rev-parse", "--is-inside-work-tree"])
        self.assertEqual((out.stdout or "").strip(), "true")

    def test_safe_argv_refuses_broad_stage(self) -> None:
        with self.assertRaises(BroadStageError):
            safe_git_argv(_REPO_ROOT, ["add", "."])


class SourceScanRegressionTests(unittest.TestCase):
    """No automation code re-introduces broad-stage or bare-cwd git writes."""

    _SCAN_DIRS = ("apps", "packages", "scripts")
    _BROAD = re.compile(r"""\[\s*["']add["']\s*,\s*["'](\.|-A|--all)["']""")

    def _py_files(self):
        for d in self._SCAN_DIRS:
            for p in (_REPO_ROOT / d).rglob("*.py"):
                if "/test" in str(p) or p.name.startswith("test_"):
                    continue
                yield p

    def test_no_broad_git_add_literal(self) -> None:
        offenders = []
        for p in self._py_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in self._BROAD.finditer(text):
                offenders.append(f"{p.relative_to(_REPO_ROOT)}: {m.group(0)}")
        self.assertEqual(offenders, [], "broad `git add .`/-A reintroduced:\n" + "\n".join(offenders))

    def test_vault_auto_push_uses_guardrail(self) -> None:
        src = (_REPO_ROOT / "apps/engineering-agent/src/yule_engineering/agents/obsidian/vault_auto_push.py").read_text(encoding="utf-8")
        self.assertIn("git_path_safety", src)
        self.assertIn("assert_safe_git_repo_path", src)
        self.assertNotRegex(src, self._BROAD)


class VaultAutoPushFunctionalTests(unittest.TestCase):
    def test_unsafe_repo_root_blocked(self) -> None:
        from yule_engineering.agents.obsidian.vault_auto_push import push_vault_if_ready

        event = type("E", (), {"status": "done", "reason": "x", "job_id": "j1"})()
        verdict = push_vault_if_ready(
            completion_event=event,
            vault_repo_root=Path(os.path.expanduser("~")),
            dry_run=False,
            env={"YULE_VAULT_AUTOPUSH_ENABLED": "true", "YULE_VAULT_BRANCH": "auto/x"},
        )
        self.assertFalse(verdict.performed)
        self.assertIn("unsafe", (verdict.blocked_reason or "").lower())

    def test_scoped_staging_only_mirror(self) -> None:
        from yule_engineering.agents.obsidian.vault_auto_push import push_vault_if_ready

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            # seed an initial commit so checkout -B works
            (repo / "README.md").write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
            # mirror change (should be staged) + an unrelated change (should NOT)
            (repo / "notes" / "vault-mirror").mkdir(parents=True)
            (repo / "notes" / "vault-mirror" / "n.md").write_text("note", encoding="utf-8")
            (repo / "unrelated.txt").write_text("do not stage me", encoding="utf-8")

            event = type("E", (), {"status": "done", "reason": "sync", "job_id": "j2"})()
            # push would fail (no remote); we only assert the commit scope, so
            # monkeypatch is overkill — instead drive up to commit via a no-push branch.
            verdict = push_vault_if_ready(
                completion_event=event,
                vault_repo_root=repo,
                dry_run=False,
                env={"YULE_VAULT_AUTOPUSH_ENABLED": "true", "YULE_VAULT_BRANCH": "auto/notes-sync"},
            )
            # push has no remote → blocked at push, but commit already happened.
            log = subprocess.run(
                ["git", "-C", str(repo), "show", "--name-only", "--format=", "auto/notes-sync"],
                capture_output=True, text=True,
            )
            committed = (log.stdout or "").strip().splitlines()
            self.assertIn("notes/vault-mirror/n.md", committed)
            self.assertNotIn("unrelated.txt", committed)


if __name__ == "__main__":
    unittest.main()
