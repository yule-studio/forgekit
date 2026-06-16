"""Worktree-root hygiene hard rails.

Pins: stale-worktree detection on disk, dry-run-by-default cleanup, refusal of
HOME / ambiguous / out-of-allowlist targets, and that a broad ``git add`` cannot
re-enter the source tree (static guard). Companion to ``test_git_path_safety``.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.governance import worktree_hygiene as wh
from yule_engineering.agents.governance.git_source_audit import (
    scan_source_for_broad_stage,
    scan_text_for_broad_stage,
)


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _age(path: Path, seconds: float) -> None:
    when = time.time() - seconds
    os.utime(path, (when, when))


class StaleDetectionTests(unittest.TestCase):
    def test_detects_old_and_skips_fresh_and_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wt"
            root.mkdir()
            fresh = root / "fresh"
            fresh.mkdir()
            stale = root / "stale"
            stale.mkdir()
            active = root / "active"
            active.mkdir()
            _age(stale, 3 * 24 * 3600)
            _age(active, 3 * 24 * 3600)  # old but pinned active => skipped

            reports = wh.detect_stale_worktree_dirs(
                root, stale_after_seconds=24 * 3600, active_paths=[active]
            )
            names = {r.path.name for r in reports}
            self.assertEqual(names, {"stale"})
            self.assertEqual(reports[0].reason, "older_than_threshold")

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(wh.detect_stale_worktree_dirs("/nonexistent/wt/root"), ())


class CleanupSafetyTests(unittest.TestCase):
    def test_dry_run_removes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _repo(tmp)
            root = tmp / "wt"
            root.mkdir()
            stale = root / "stale"
            stale.mkdir()
            (stale / "f").write_text("x")
            _age(stale, 3 * 24 * 3600)
            allow = [root]
            detected = wh.detect_stale_worktree_dirs(root, stale_after_seconds=3600)

            plan = wh.plan_worktree_cleanup(detected, repo_root=repo, allow_roots=allow, apply=False)
            self.assertFalse(plan.applied)
            self.assertEqual([p.name for p in plan.would_remove], ["stale"])
            self.assertEqual(plan.removed, ())
            self.assertTrue(stale.exists())  # untouched

    def test_apply_removes_only_allowlisted_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _repo(tmp)
            root = tmp / "wt"
            root.mkdir()
            stale = root / "stale"
            stale.mkdir()
            (stale / "f").write_text("x")
            _age(stale, 3 * 24 * 3600)
            allow = [root]
            detected = wh.detect_stale_worktree_dirs(root, stale_after_seconds=3600)

            plan = wh.plan_worktree_cleanup(detected, repo_root=repo, allow_roots=allow, apply=True)
            self.assertTrue(plan.applied)
            self.assertEqual([p.name for p in plan.removed], ["stale"])
            self.assertFalse(stale.exists())

    def test_refuses_home_even_with_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _repo(tmp)
            allow = [tmp / "wt"]
            (tmp / "wt").mkdir()
            home_entry = wh.StaleWorktreeDir(
                path=Path(os.path.expanduser("~")), age_seconds=9e9, reason="x"
            )
            plan = wh.plan_worktree_cleanup(
                [home_entry], repo_root=repo, allow_roots=allow, apply=True
            )
            self.assertEqual(plan.removed, ())
            self.assertEqual(len(plan.refused), 1)
            self.assertIn("HOME", plan.refused[0][1])

    def test_refuses_non_allowlisted_and_root_itself_and_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _repo(tmp)
            root = tmp / "wt"
            root.mkdir()
            allow = [root]
            for bad, why in [
                (tmp / "elsewhere" / "x", "not a direct child"),
                (root, "ROOT itself"),
                (repo, "repo"),
                (repo / ".git", ".git"),
                ("", "ambiguous"),
            ]:
                with self.assertRaises(wh.UnsafeCleanupError):
                    wh.assert_safe_cleanup_target(bad, allow_roots=allow, repo_root=repo)


class AllowlistTests(unittest.TestCase):
    def test_env_override_and_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            roots = wh.allowlisted_roots(repo, env={})
            self.assertEqual(roots[0], Path(wh.DEFAULT_CODING_EXECUTOR_ROOT).resolve())
            self.assertEqual(
                roots[1], (repo / wh.DEFAULT_SELF_IMPROVEMENT_ROOT_REL).resolve()
            )

            custom = Path(tmp) / "custom-wt"
            roots2 = wh.allowlisted_roots(
                repo, env={wh.ENV_CODING_EXECUTOR_WORKTREE_ROOT: str(custom)}
            )
            self.assertEqual(roots2[0], custom.resolve())


class DiskUsageTests(unittest.TestCase):
    def test_reports_repo_paths_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp))
            (repo / "runs").mkdir()
            (repo / "runs" / "f").write_text("hello")
            entries = {e.label: e for e in wh.summarize_disk_usage(repo, env={})}
            self.assertTrue(entries["repo runs"].exists)
            self.assertEqual(entries["repo runs"].entries, 1)
            self.assertFalse(entries["repo .cache"].exists)
            # read-only: nothing created
            self.assertFalse((repo / ".cache").exists())


class SourceScanTests(unittest.TestCase):
    def test_clean_tree_has_no_findings(self) -> None:
        # The live source tree must never contain an executable broad stage.
        repo_root = Path(__file__).resolve().parents[2]
        roots = [repo_root / "apps", repo_root / "packages", repo_root / "scripts"]
        findings = scan_source_for_broad_stage([r for r in roots if r.exists()])
        self.assertEqual(findings, (), msg="\n".join(f"{f.path}:{f.lineno} {f.text}" for f in findings))

    def test_argv_broad_stage_reintroduction_caught(self) -> None:
        snippet = 'subprocess.run(["git", "-C", repo, "add", "."])\n'
        findings = scan_text_for_broad_stage(Path("evil.py"), snippet)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "argv_add")

    def test_shell_broad_stage_and_commit_caught(self) -> None:
        for snippet, kind in [
            ('run("git add -A")\n', "shell_add"),
            ('os.system("git commit -a -m x")\n', "shell_commit"),
            ('cmd = ["commit", "--all"]\n', "argv_commit"),
        ]:
            findings = scan_text_for_broad_stage(Path("evil.py"), snippet)
            self.assertTrue(findings, snippet)
            self.assertEqual(findings[0].kind, kind, snippet)

    def test_prose_and_explicit_pathspec_not_flagged(self) -> None:
        safe = (
            '# never use `git add .` here\n'
            '"""docstring mentioning git add -A in backticks `git add -A`"""\n'
            'subprocess.run(["git", "-C", repo, "add", "--", rel])\n'
            'subprocess.run(["git", "-C", repo, "commit", "-m", msg])\n'
        )
        self.assertEqual(scan_text_for_broad_stage(Path("ok.py"), safe), ())


if __name__ == "__main__":
    unittest.main()
