"""Source-code safe-class mutation (WT3 #240) — real edit under hard rails.

source-format is the ONLY source action: whitespace-only, semantics-preserving,
parse-verified, with rollback. These tests prove the rails actually bite — off by
default, allowlist + ext enforced, cap enforced, broken/already-clean files skipped,
a verify failure rolls back to the exact original, and the non-whitespace guard holds.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.autopilot import runner as R
from forgekit_console.autopilot.runner import ACTION_SOURCE_FORMAT, BoundedMutator, ExecTask

SRC_PREFIX = "apps/forgekit-console/src/"


class SourceFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.rel = SRC_PREFIX + "pkg/mod.py"
        self.target = self.tmp / self.rel
        self.target.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, text: str) -> None:
        self.target.write_text(text, encoding="utf-8")

    def _mutator(self, **kw):
        return BoundedMutator(self.tmp, source_prefixes=(SRC_PREFIX,), **kw)

    def test_off_by_default(self) -> None:
        # no source_prefixes → source mutation refused (safe default)
        self._write("x = 1   \n")
        out = BoundedMutator(self.tmp).execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertFalse(out.executed)
        self.assertIn("비활성", out.refused_reason)

    def test_real_whitespace_fix_executes_and_parses(self) -> None:
        self._write("def f():   \n    return 1   \n\n\n")   # trailing ws + extra blank lines
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertTrue(out.executed)
        self.assertTrue(out.verified)
        after = self.target.read_text(encoding="utf-8")
        self.assertEqual(after, "def f():\n    return 1\n")     # real change, no trailing ws
        compile(after, "mod.py", "exec")                        # still valid python

    def test_semantics_preserved_invariant(self) -> None:
        # the transform may only move whitespace — non-whitespace content is identical
        before = "a=1  \nb = 2\t\n"
        self._write(before)
        self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        after = self.target.read_text(encoding="utf-8")
        self.assertEqual("".join(after.split()), "".join(before.split()))

    def test_forbidden_path_blocked(self) -> None:
        # a path outside the enabled source prefix is refused
        self._write("x = 1  \n")
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, "other/place/mod.py"))
        self.assertFalse(out.executed)
        self.assertIn("경로 불허", out.refused_reason)

    def test_non_py_extension_blocked(self) -> None:
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, SRC_PREFIX + "pkg/data.txt"))
        self.assertFalse(out.executed)
        self.assertIn("경로 불허", out.refused_reason)

    def test_traversal_blocked(self) -> None:
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, SRC_PREFIX + "../../etc/x.py"))
        self.assertFalse(out.executed)

    def test_already_clean_is_noop(self) -> None:
        self._write("x = 1\n")
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertFalse(out.executed)
        self.assertIn("no-op", out.refused_reason)

    def test_unparseable_file_skipped(self) -> None:
        self._write("def f(:  \n")          # syntax error → never touched
        out = self._mutator().execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertFalse(out.executed)
        self.assertIn("파싱 불가", out.refused_reason)

    def test_over_cap_blocked(self) -> None:
        # many trailing-whitespace lines → diff exceeds the cap → refused, no write
        self._write("".join(f"x{i} = {i}   \n" for i in range(20)))
        out = self._mutator(max_diff_lines=5).execute(ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertFalse(out.executed)
        self.assertIn("한도", out.refused_reason)

    def test_failed_verify_rolls_back(self) -> None:
        before = "y = 2   \n"
        self._write(before)
        # inject a verifier that fails → the write must be rolled back to the exact original
        out = self._mutator(source_verifier=lambda txt, p: False).execute(
            ExecTask(ACTION_SOURCE_FORMAT, self.rel))
        self.assertFalse(out.executed)
        self.assertIn("rollback", out.refused_reason)
        self.assertEqual(self.target.read_text(encoding="utf-8"), before)   # original restored


if __name__ == "__main__":
    unittest.main()
