"""repo-autopilot WT2 — mode + executor arbitration (one executor, repo allowlist).

Proves: arbitration is a single-slot lock (two can't hold at once), autopilot refuses
non-allowlisted repos, safe findings execute (one executor at a time) while risky/
restricted are proposed-only, and the kill switch halts. Pure → bare CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP
from forgekit_console.autopilot.orchestrator import (
    AutopilotOrchestrator,
    ExecutorArbiter,
)


class ArbiterTests(unittest.TestCase):
    def test_single_slot_mutual_exclusion(self) -> None:
        arb = ExecutorArbiter()
        self.assertTrue(arb.acquire("fe"))      # fe holds
        self.assertFalse(arb.acquire("be"))     # be cannot — queued
        self.assertEqual(arb.holder, "fe")
        self.assertIn("be", arb.queued)
        arb.release("fe")
        self.assertEqual(arb.holder, "be")      # handed to the next in queue
        arb.release("be")
        self.assertIsNone(arb.holder)


class OrchestratorTests(unittest.TestCase):
    def _findings(self):
        return [
            AP.RepoFinding("forgekit", "docs 보강", kind="docs"),         # safe
            AP.RepoFinding("forgekit", "lint 정리", kind="lint"),          # safe
            AP.RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap"),  # risky
            AP.RepoFinding("forgekit", "프로덕션 배포", kind="ops"),         # restricted
        ]

    def _risk(self, f):
        if "배포" in f.finding:
            return "blocked"
        if "rewrite" in f.finding:
            return "risky"
        return "safe"

    def test_non_allowlisted_repo_refused(self) -> None:
        orch = AutopilotOrchestrator()
        res = orch.run_cycle("random-repo", self._findings(), risk_of=self._risk)
        self.assertTrue(res.blocked_repo)
        self.assertEqual(res.executed, [])

    def test_allowlisted_repo_executes_safe_only_one_executor(self) -> None:
        import tempfile
        from pathlib import Path
        from forgekit_console.autopilot import BoundedMutator

        repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        orch = AutopilotOrchestrator(mutator=BoundedMutator(repo))  # real execution
        res = orch.run_cycle("forgekit", self._findings(), risk_of=self._risk)
        # safe findings executed; risky/restricted proposed-only
        self.assertTrue(res.executed)
        self.assertTrue(all("docs" in e["finding"] or "lint" in e["finding"] for e in res.executed))
        proposed = {p.get("decision_class") for p in res.proposed}
        self.assertIn("risky", proposed)
        self.assertIn("blocked", proposed)
        # executor log shows serial grant/release — never two holders at once
        grants = [x for x in res.executor_log if x.startswith("grant:")]
        releases = [x for x in res.executor_log if x.startswith("release:")]
        self.assertEqual(len(grants), len(releases))   # every grant released → serial

    def test_kill_switch_halts(self) -> None:
        orch = AutopilotOrchestrator(kill_switch=True)
        res = orch.run_cycle("forgekit", self._findings(), risk_of=self._risk)
        self.assertTrue(res.halted)
        self.assertEqual(res.executed, [])

    def test_phases_present(self) -> None:
        import tempfile
        from pathlib import Path
        from forgekit_console.autopilot import BoundedMutator

        repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        orch = AutopilotOrchestrator(mutator=BoundedMutator(repo))  # real exec → execute/verify
        res = orch.run_cycle("forgekit", [AP.RepoFinding("forgekit", "docs 보강", kind="docs")],
                             risk_of=self._risk)
        joined = " ".join(res.steps)
        for phase in ("observe", "classify", "pm_structure", "gateway_route",
                      "tech_lead_signoff", "execute", "verify", "record"):
            self.assertIn(phase, joined)


if __name__ == "__main__":
    unittest.main()
