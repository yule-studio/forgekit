"""Allowlist cleanup — classification, dry-run/execute, protected non-deletion (item G)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.cleanup import (
    CLEANUP_SAFE_MARKER,
    Classification,
    classify,
    run_cleanup,
    scan,
)


def _build_tree(root: Path) -> None:
    # PRESERVE — audit / canonical / source
    (root / "workflow.sqlite3").write_text("binary-ish", encoding="utf-8")
    (root / "keep.py").write_text("print('x')", encoding="utf-8")
    (root / "10-projects" / "p" / "task-logs").mkdir(parents=True)
    (root / "10-projects" / "p" / "task-logs" / "task-log-x.md").write_text("note", encoding="utf-8")
    (root / "agent_ops_audit.json").write_text("[]", encoding="utf-8")
    # DELETABLE — transient / generated
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "mod.pyc").write_text("cache", encoding="utf-8")
    (root / "data.tmp").write_text("temp", encoding="utf-8")
    (root / "report-snapshot-2026.json").write_text("snap", encoding="utf-8")
    (root / "marker.txt").write_text(f"safe {CLEANUP_SAFE_MARKER} regen", encoding="utf-8")
    # APPROVAL_NEEDED — generated-but-tracked harness
    (root / ".claude" / "skills" / "x").mkdir(parents=True)
    (root / ".claude" / "skills" / "x" / "SKILL.md").write_text("gen", encoding="utf-8")


class ClassifyTests(unittest.TestCase):
    def test_preserve_wins_over_transient_location(self) -> None:
        # a sqlite inside an exports dir is still preserved
        cls, rule, _ = classify("exports/workflow.sqlite3", is_dir=False)
        self.assertEqual(cls, Classification.PRESERVE)

    def test_pyc_is_deletable(self) -> None:
        cls, _r, _ = classify("build/mod.pyc", is_dir=False)
        self.assertEqual(cls, Classification.DELETABLE)

    def test_generated_harness_needs_approval(self) -> None:
        cls, _r, _ = classify(".claude/skills/x/SKILL.md", is_dir=False)
        self.assertEqual(cls, Classification.APPROVAL_NEEDED)

    def test_unmatched_defaults_to_preserve(self) -> None:
        cls, rule, _ = classify("weird/thing.bin", is_dir=False)
        self.assertEqual(cls, Classification.PRESERVE)
        self.assertEqual(rule, "preserve:default")


class ScanDryRunTests(unittest.TestCase):
    def test_dry_run_deletes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_tree(root)
            receipt = run_cleanup(root)  # dry-run default
            self.assertEqual(receipt.status, "dry_run")
            self.assertEqual(receipt.deleted_count, 0)
            self.assertTrue((root / "data.tmp").exists())  # still there
            # classifications populated
            rels = {e.rel_path for e in receipt.deletable}
            self.assertIn("data.tmp", rels)
            self.assertIn("report-snapshot-2026.json", rels)
            self.assertIn("__pycache__", rels)  # whole-dir unit
            self.assertIn("marker.txt", rels)  # promoted by marker
            approval = {e.rel_path for e in receipt.approval_needed}
            self.assertIn(".claude/skills/x/SKILL.md", approval)
            self.assertGreater(receipt.reclaimable_bytes, 0)

    def test_protected_are_listed_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_tree(root)
            receipt = run_cleanup(root)
            protected = {e.rel_path for e in receipt.protected}
            self.assertIn("workflow.sqlite3", protected)
            self.assertIn("keep.py", protected)
            self.assertIn("agent_ops_audit.json", protected)
            self.assertIn("10-projects/p/task-logs/task-log-x.md", protected)


class ExecuteTests(unittest.TestCase):
    def test_execute_without_confirm_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_tree(root)
            receipt = run_cleanup(root, execute=True, confirm=False)
            self.assertEqual(receipt.status, "dry_run")
            self.assertEqual(receipt.deleted_count, 0)
            self.assertTrue((root / "data.tmp").exists())
            self.assertTrue(any("refusing to delete" in w for w in receipt.warnings))

    def test_execute_with_confirm_removes_only_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_tree(root)
            receipt = run_cleanup(root, execute=True, confirm=True)
            self.assertEqual(receipt.status, "executed")
            self.assertGreater(receipt.deleted_count, 0)
            # deletable gone
            self.assertFalse((root / "data.tmp").exists())
            self.assertFalse((root / "__pycache__").exists())
            self.assertFalse((root / "report-snapshot-2026.json").exists())
            # PROTECTED survive — audit/canonical never deleted
            self.assertTrue((root / "workflow.sqlite3").exists())
            self.assertTrue((root / "keep.py").exists())
            self.assertTrue((root / "agent_ops_audit.json").exists())
            self.assertTrue((root / "10-projects" / "p" / "task-logs" / "task-log-x.md").exists())
            # APPROVAL_NEEDED survive — never auto-deleted
            self.assertTrue((root / ".claude" / "skills" / "x" / "SKILL.md").exists())

    def test_missing_root_warns(self) -> None:
        receipt = run_cleanup(Path("/nonexistent/yule/cleanup/root"))
        self.assertTrue(any("does not exist" in w for w in receipt.warnings))
        self.assertEqual(receipt.scanned_count, 0)


if __name__ == "__main__":
    unittest.main()
