"""repo-autopilot end-to-end (WT6) — the team flow closes, with all rails holding.

observe → internal chain (PM→gateway→tech-lead) → orchestrate (one executor, safe-only)
→ operator digest. Asserts: internal signoff required, single-executor serial, safe
auto / risky→user / restricted→blocked, repo allowlist, and a vault trace. Pure → CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP
from forgekit_console import vault
from forgekit_console.autopilot import execution as EX


class AutopilotEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "a.py").write_text("# TODO x\n# TODO y\n# FIXME z\n", encoding="utf-8")

    def test_full_team_flow_with_rails(self) -> None:
        # observe → findings (incl. honest figma-not-connected UI discomfort)
        findings = AP.observe_repo("forgekit", self.tmp,
                                   ui_discomfort=["버튼 간격이 좁다"])
        self.assertTrue(findings)

        # add explicit risky + restricted to prove classification
        findings += [AP.RepoFinding("forgekit", "auth 대규모 rewrite", kind="gap"),
                     AP.RepoFinding("forgekit", "프로덕션 배포", kind="ops")]
        risk = lambda f: "blocked" if "배포" in f.finding else ("risky" if "rewrite" in f.finding else "safe")

        res = AP.AutopilotOrchestrator(mutator=AP.BoundedMutator(self.tmp)).run_cycle(
            "forgekit", findings, risk_of=risk)
        # safe executed; risky/restricted proposed; single executor serial
        self.assertTrue(res.executed)
        grants = [x for x in res.executor_log if x.startswith("grant:")]
        rels = [x for x in res.executor_log if x.startswith("release:")]
        self.assertEqual(len(grants), len(rels))   # serial — never two holders

        digest = EX.build_operator_digest([res])
        self.assertTrue(digest.auto_executed)
        self.assertTrue(digest.needs_user)
        self.assertTrue(digest.blocked)

        # a vault trace note records who/why/approval for an executed step
        _, _, decision, trace = AP.run_internal_chain(
            AP.RepoFinding("forgekit", "docs 보강", kind="docs"), risk_class="safe")
        note = AP.trace_note("be", decision, what="docs 보강", trace=trace)
        authored = vault.build_authored_note(
            "tech-lead", "autopilot trace", "\n".join(note.approval_chain),
            created_at="2026-06-18", phase="tech-lead", source_flow="repo-autopilot")
        self.assertIn("agent_author: tech-lead", authored)
        self.assertIn("source_flow: repo-autopilot", authored)

    def test_non_allowlisted_repo_never_runs(self) -> None:
        res = AP.AutopilotOrchestrator().run_cycle(
            "some-random-repo", [AP.RepoFinding("x", "docs", kind="docs")], risk_of=lambda f: "safe")
        self.assertTrue(res.blocked_repo)
        self.assertEqual(res.executed, [])

    def test_no_execution_without_internal_signoff(self) -> None:
        # the hard rule end-to-end: no TechLeadDecision → specialist cannot execute
        self.assertFalse(AP.can_specialist_execute(None))


if __name__ == "__main__":
    unittest.main()
