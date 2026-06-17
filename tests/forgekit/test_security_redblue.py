"""Red/blue security mode (WT5) — HARD safety gates. Own-assets only, no auto-exec.

Proves: non-allowlisted / public / third-party targets are BLOCKED (no usable plan),
eligible own targets default to PLAN-ONLY (dry-run), an active drill needs explicit
approval, and purple synthesis yields a blue DefenseRunbook. Pure → bare CI.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import security as S
from forgekit_console.security import contract as C


class TargetGateTests(unittest.TestCase):
    def test_public_or_third_party_target_blocked(self) -> None:
        for tid in ("example.com", "https://victim.org", "8.8.8.8", "competitor-prod"):
            pkt = S.build_drill(tid)
            self.assertEqual(pkt.status, C.DRILL_BLOCKED)
            self.assertFalse(pkt.target.eligible)
            self.assertIn("allowlist", pkt.refusal_reason)
            self.assertFalse(pkt.executed)  # nothing ran

    def test_non_isolated_or_non_allowlisted_blocked(self) -> None:
        al = {"shared": C.TargetSpec("shared", C.TARGET_OWN_SERVER, allowlisted=True, isolated=False)}
        self.assertEqual(S.build_drill("shared", allowlist=al).status, C.DRILL_BLOCKED)


class PlanFirstTests(unittest.TestCase):
    def test_eligible_target_defaults_to_plan_only(self) -> None:
        pkt = S.build_drill("k3s-isolated")        # default allowlist, own isolated k3s
        self.assertEqual(pkt.status, C.DRILL_PLAN_ONLY)
        self.assertTrue(pkt.attack_plan.dry_run)   # dry-run by default
        self.assertTrue(pkt.requires_approval)
        self.assertFalse(pkt.executed)             # NOT executed without approval
        self.assertTrue(pkt.defense_runbook.hardening)  # blue output present

    def test_active_drill_requires_explicit_approval(self) -> None:
        # without approval → plan-only; with approval → active (operator-gated upstream)
        self.assertFalse(S.build_drill("k3s-isolated", approved=False).executed)
        approved = S.build_drill("k3s-isolated", approved=True)
        self.assertEqual(approved.status, C.DRILL_APPROVED_ACTIVE)
        self.assertFalse(approved.attack_plan.dry_run)
        self.assertTrue(approved.executed)

    def test_no_offensive_tooling_only_plan_and_defense(self) -> None:
        pkt = S.build_drill("localhost")
        # the plan is hypotheses + read-only checks, not exploit commands
        self.assertTrue(pkt.attack_plan.hypotheses)
        self.assertTrue(all("읽기" in c or "점검" in c or "인벤토리" in c
                            for c in pkt.attack_plan.checks))


class PurpleTests(unittest.TestCase):
    def test_purple_synthesizes_defense_runbook(self) -> None:
        red = S.build_drill("k3s-isolated").attack_plan
        rb = S.synthesize_purple(red, findings=("열린 디버그 포트",))
        self.assertTrue(rb.hardening)
        self.assertTrue(any("열린 디버그 포트" in m for m in rb.mitigation))

    def test_k3s_runbook_documents_isolation(self) -> None:
        md = S.k3s_isolation_runbook()
        self.assertIn("namespace", md)
        self.assertIn("dry-run", md)
        self.assertIn("공용 인터넷", md)


if __name__ == "__main__":
    unittest.main()
