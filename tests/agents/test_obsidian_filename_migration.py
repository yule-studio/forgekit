"""F8 / #99 — Obsidian 파일명 마이그레이션 unit tests.

dry-run 안전 / rename 매핑 / wikilink 갱신 / frontmatter 주입 /
보호 브랜치 가드 / 충돌 SKIP — 6개 hard rail 을 ≤15 케이스로 핀.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import migrate_obsidian_filenames as mig  # noqa: E402


class PureHelperTests(unittest.TestCase):
    def test_compute_new_name_strips_date_and_canonicalises(self) -> None:
        self.assertEqual(
            mig.compute_new_name("2026-05-08_task-log-issue-25.md"),
            "task-log-issue-25.md",
        )

    def test_compute_new_name_moves_issue_to_suffix(self) -> None:
        # issue-<n>-<kind>-… → <kind>-…-issue-<n>
        self.assertEqual(
            mig.compute_new_name("2026-05-09_issue-73-task-log-foo-bar.md"),
            "task-log-foo-bar-issue-73.md",
        )

    def test_compute_new_name_converts_underscore_to_hyphen(self) -> None:
        # task-log_25-ecc → task-log-25-ecc
        self.assertEqual(
            mig.compute_new_name("2026-05-08_task-log_25-ecc.md"),
            "task-log-25-ecc.md",
        )

    def test_compute_new_name_returns_none_for_no_prefix(self) -> None:
        self.assertIsNone(mig.compute_new_name("task-log-issue-25.md"))

    def test_extract_date_prefix_pulls_iso_date(self) -> None:
        self.assertEqual(
            mig.extract_date_prefix("2026-05-11_decision-foo.md"),
            "2026-05-11",
        )

    def test_update_wikilinks_replaces_known_targets(self) -> None:
        text = "see [[2026-05-08_research_ecc]] and [[unrelated]]"
        new, count = mig.update_wikilinks(text, {"2026-05-08_research_ecc": "research-ecc"})
        self.assertEqual(count, 1)
        self.assertIn("[[research-ecc]]", new)
        self.assertIn("[[unrelated]]", new)

    def test_update_wikilinks_preserves_tail_pipes(self) -> None:
        text = "[[2026-05-08_research_ecc|ECC]]"
        new, count = mig.update_wikilinks(text, {"2026-05-08_research_ecc": "research-ecc"})
        self.assertEqual(count, 1)
        self.assertIn("[[research-ecc|ECC]]", new)

    def test_ensure_created_at_injects_when_missing(self) -> None:
        content = "---\ntitle: foo\nkind: task-log\n---\n\nbody"
        new, did = mig.ensure_created_at_in_frontmatter(content, date="2026-05-08")
        self.assertTrue(did)
        self.assertIn("created_at: 2026-05-08T00:00:00+09:00", new)

    def test_ensure_created_at_skips_when_present(self) -> None:
        content = "---\ntitle: foo\ncreated_at: 2024-01-01T00:00:00+09:00\n---\nbody"
        new, did = mig.ensure_created_at_in_frontmatter(content, date="2026-05-08")
        self.assertFalse(did)
        self.assertEqual(new, content)


class _TempVaultMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "checkout", "-qb", "feature/migration"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Test"],
            check=True,
        )
        self.notes = self.root / "notes"
        self.notes.mkdir()

    def _write(self, relpath: str, body: str) -> Path:
        p = self.notes / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(p)], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", f"add {relpath}"],
            check=True,
        )
        return p


class DryRunTests(_TempVaultMixin, unittest.TestCase):
    def test_dry_run_does_not_modify_filesystem(self) -> None:
        src = self._write(
            "task-logs/2026-05-08_task-log-foo.md",
            "---\ntitle: foo\nkind: task-log\n---\nbody [[2026-05-08_task-log-foo]]\n",
        )
        report = mig.run_migration(
            notes_root=self.notes, repo_root=self.root, apply=False
        )
        self.assertFalse(report.apply)
        self.assertTrue(src.exists())
        names = [p.source.name for p in report.renames]
        self.assertIn("2026-05-08_task-log-foo.md", names)
        # wikilink 갱신은 dry-run 에서도 reported 되어야 함
        self.assertGreaterEqual(report.wikilink_count, 1)


class ApplyTests(_TempVaultMixin, unittest.TestCase):
    def test_apply_renames_file_and_updates_wikilink(self) -> None:
        src = self._write(
            "task-logs/2026-05-08_task-log-foo.md",
            "---\ntitle: foo\nkind: task-log\n---\nbody\n",
        )
        # 참조 파일은 새 컨벤션 (날짜 prefix 없음) — rename 대상 아님
        ref = self._write(
            "decisions/decision-bar.md",
            "---\ntitle: bar\nkind: decision\ncreated_at: 2025-01-01T00:00:00+09:00\n---\nsee [[2026-05-08_task-log-foo]]\n",
        )
        report = mig.run_migration(
            notes_root=self.notes, repo_root=self.root, apply=True
        )
        self.assertTrue(report.apply)
        self.assertIsNone(report.blocker)
        self.assertFalse(src.exists())
        new = self.notes / "task-logs" / "task-log-foo.md"
        self.assertTrue(new.exists())
        # created_at 자동 주입 확인
        self.assertIn("created_at: 2026-05-08T00:00:00+09:00", new.read_text())
        # wikilink 갱신
        self.assertIn("[[task-log-foo]]", ref.read_text())

    def test_protected_branch_apply_is_blocked(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), "checkout", "-qb", "main"], check=True
        )
        self._write(
            "task-logs/2026-05-08_task-log-x.md",
            "---\ntitle: x\nkind: task-log\n---\nbody\n",
        )
        report = mig.run_migration(
            notes_root=self.notes, repo_root=self.root, apply=True
        )
        self.assertIsNotNone(report.blocker)
        self.assertIn("protected branch", report.blocker or "")

    def test_collision_target_is_skipped(self) -> None:
        self._write(
            "task-logs/2026-05-08_task-log-foo.md",
            "---\ntitle: a\nkind: task-log\n---\n",
        )
        # 동일 stem 의 새 파일을 미리 생성 → 충돌
        self._write(
            "task-logs/task-log-foo.md",
            "---\ntitle: b\nkind: task-log\n---\n",
        )
        report = mig.run_migration(
            notes_root=self.notes, repo_root=self.root, apply=True
        )
        skips = [p for p in report.renames if p.skipped_reason]
        self.assertEqual(len(skips), 1)
        self.assertIn("already exists", skips[0].skipped_reason or "")


if __name__ == "__main__":
    unittest.main()
