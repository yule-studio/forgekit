"""Bounded execution runner (WT3) — REAL safe-class mutation, capped + verified. Pure.

Proves: a note write actually changes a file under an allowed prefix and verifies;
non-safe actions / out-of-prefix / over-cap paths are REFUSED (not executed); and
without a mutator the orchestrator records propose-only (no fake executed=true).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP
from forgekit_console.autopilot import runner as R


class MutatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))
        self.mut = R.BoundedMutator(self.repo)

    def test_note_write_is_real_and_verified(self) -> None:
        task = R.ExecTask(R.ACTION_NOTE, "runs/forgekit/autopilot/x.md", content="# hi\nbody\n")
        out = self.mut.execute(task)
        self.assertTrue(out.executed)
        self.assertTrue(out.verified)
        target = self.repo / "runs/forgekit/autopilot/x.md"
        self.assertTrue(target.exists())                 # REAL file written
        self.assertIn("body", target.read_text(encoding="utf-8"))
        self.assertNotEqual(out.before_hash, out.after_hash)

    def test_format_strips_trailing_whitespace(self) -> None:
        t = self.repo / "docs/note.md"
        t.parent.mkdir(parents=True)
        t.write_text("line1   \nline2\t\n", encoding="utf-8")
        out = self.mut.execute(R.ExecTask(R.ACTION_FORMAT, "docs/note.md"))
        self.assertTrue(out.executed)
        self.assertEqual(t.read_text(encoding="utf-8"), "line1\nline2\n")

    def test_refuses_non_safe_action(self) -> None:
        out = self.mut.execute(R.ExecTask("deploy", "runs/x.md", content="x"))
        self.assertFalse(out.executed)
        self.assertIn("non-safe", out.refused_reason)

    def test_refuses_out_of_prefix_and_traversal(self) -> None:
        for rp in ("src/forgekit_console/app.py", "../escape.md", "/etc/passwd"):
            out = self.mut.execute(R.ExecTask(R.ACTION_NOTE, rp, content="x"))
            self.assertFalse(out.executed, rp)
            self.assertTrue(out.refused_reason)

    def test_over_diff_cap_refused(self) -> None:
        mut = R.BoundedMutator(self.repo, max_diff_lines=3)
        out = mut.execute(R.ExecTask(R.ACTION_NOTE, "runs/big.md", content="\n".join("x" * 1 for _ in range(50))))
        self.assertFalse(out.executed)
        self.assertIn("diff", out.refused_reason)

    def test_noop_is_not_fake_success(self) -> None:
        t = self.repo / "runs/same.md"
        t.parent.mkdir(parents=True)
        t.write_text("identical", encoding="utf-8")
        out = self.mut.execute(R.ExecTask(R.ACTION_NOTE, "runs/same.md", content="identical"))
        self.assertFalse(out.executed)   # no change → NOT executed (honest)


class OrchestratorMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))

    def test_without_mutator_no_fake_execution(self) -> None:
        # the key anti-fake invariant: no mutator → propose-only, executed empty
        res = AP.AutopilotOrchestrator().run_cycle(
            "forgekit", [AP.RepoFinding("forgekit", "docs 보강", kind="docs")],
            risk_of=lambda f: "safe")
        self.assertEqual(res.executed, [])
        self.assertTrue(res.proposed)

    def test_with_mutator_real_execution(self) -> None:
        mut = AP.BoundedMutator(self.repo)
        res = AP.AutopilotOrchestrator(mutator=mut).run_cycle(
            "forgekit", [AP.RepoFinding("forgekit", "docs 보강", kind="docs")],
            risk_of=lambda f: "safe")
        self.assertTrue(res.executed)
        e = res.executed[0]
        self.assertTrue(e["verified"])
        self.assertTrue((self.repo / e["path"]).exists())  # the note really exists

    def test_risky_and_blocked_never_execute_even_with_mutator(self) -> None:
        mut = AP.BoundedMutator(self.repo)
        findings = [AP.RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap"),
                    AP.RepoFinding("forgekit", "프로덕션 배포", kind="ops")]
        risk = lambda f: "blocked" if "배포" in f.finding else "risky"
        res = AP.AutopilotOrchestrator(mutator=mut).run_cycle("forgekit", findings, risk_of=risk)
        self.assertEqual(res.executed, [])      # risky/restricted never auto-executed
        self.assertTrue(res.proposed)


if __name__ == "__main__":
    unittest.main()
