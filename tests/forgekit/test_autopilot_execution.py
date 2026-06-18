"""repo-autopilot WT4 — safe-class execution validation + operator digest.

Proves: validate_execution refuses without internal signoff / over limits / non-safe,
the safe-class allowlist and auto-forbidden sets are fixed, and the operator digest
separates auto-executed (internal-only) from needs-user and blocked. Pure → CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP
from forgekit_console.autopilot import execution as EX
from forgekit_console.autopilot.artifacts import ExecutionTaskSplit, TechLeadDecision
from forgekit_console.autopilot.orchestrator import AutopilotLimits


class ValidateTests(unittest.TestCase):
    def _safe_decision(self):
        return TechLeadDecision("docs 보강", "safe", AP.L2_INTERNAL_APPROVE, can_execute=True)

    def _split(self):
        return ExecutionTaskSplit("docs 보강", executor="be", tasks=("docs",))

    def test_safe_within_limits_allowed(self) -> None:
        ok, reasons = EX.validate_execution(self._safe_decision(), self._split(),
                                            AutopilotLimits(), diff=10, files=1, risk=0.2)
        self.assertTrue(ok)
        self.assertEqual(reasons, ())

    def test_no_internal_signoff_refused(self) -> None:
        risky = TechLeadDecision("x", "risky", AP.L3_USER_APPROVE, can_execute=False)
        ok, reasons = EX.validate_execution(risky, self._split(), AutopilotLimits())
        self.assertFalse(ok)
        self.assertTrue(any("승인" in r for r in reasons))

    def test_over_limit_refused(self) -> None:
        ok, reasons = EX.validate_execution(self._safe_decision(), self._split(),
                                            AutopilotLimits(max_diff=5), diff=100)
        self.assertFalse(ok)
        self.assertTrue(any("diff" in r for r in reasons))

    def test_allowlists_are_fixed(self) -> None:
        self.assertIn("docs", EX.SAFE_CLASS_ALLOWLIST)
        self.assertIn("deploy", EX.AUTO_FORBIDDEN)
        self.assertIn("secret", EX.AUTO_FORBIDDEN)
        # safe and forbidden never overlap
        self.assertEqual(set(EX.SAFE_CLASS_ALLOWLIST) & set(EX.AUTO_FORBIDDEN), set())


class DigestTests(unittest.TestCase):
    def test_digest_separates_auto_from_user_and_blocked(self) -> None:
        import tempfile
        from pathlib import Path

        repo = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        orch = AP.AutopilotOrchestrator(mutator=AP.BoundedMutator(repo))
        findings = [
            AP.RepoFinding("forgekit", "docs 보강", kind="docs"),
            AP.RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap"),
            AP.RepoFinding("forgekit", "프로덕션 배포", kind="ops"),
        ]
        risk = lambda f: "blocked" if "배포" in f.finding else ("risky" if "rewrite" in f.finding else "safe")
        res = orch.run_cycle("forgekit", findings, risk_of=risk)
        digest = EX.build_operator_digest([res])
        self.assertTrue(digest.auto_executed)    # safe docs ran (internal-approved)
        self.assertTrue(digest.needs_user)        # risky → user
        self.assertTrue(digest.blocked)           # restricted → blocked
        lines = digest.lines()
        self.assertTrue(any("내 승인 없이 가능" in ln for ln in lines))  # clarity line


if __name__ == "__main__":
    unittest.main()
