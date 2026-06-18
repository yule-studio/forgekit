"""repo-autopilot WT1 — internal approval chain (no internal signoff, no execution).

Proves: a safe finding clears the internal chain (L2 → executable WITHOUT user), a
risky finding stops at user-approval (L3, not executable), a restricted finding is
L4 (never auto), and a specialist CANNOT execute without a TechLeadDecision. Pure.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import autopilot as AP


class ApprovalLevelTests(unittest.TestCase):
    def test_classify_levels(self) -> None:
        self.assertEqual(AP.classify_level("docs 보강"), AP.L2_INTERNAL_APPROVE)
        self.assertEqual(AP.classify_level("auth 대규모 rewrite"), AP.L3_USER_APPROVE)
        self.assertEqual(AP.classify_level("프로덕션 배포"), AP.L4_RESTRICTED)
        self.assertEqual(AP.classify_level("secret 회전"), AP.L4_RESTRICTED)

    def test_only_internal_safe_is_autopilot_executable(self) -> None:
        self.assertTrue(AP.autopilot_can_execute(AP.L2_INTERNAL_APPROVE))
        self.assertFalse(AP.autopilot_can_execute(AP.L3_USER_APPROVE))
        self.assertFalse(AP.autopilot_can_execute(AP.L4_RESTRICTED))
        self.assertTrue(AP.needs_user(AP.L3_USER_APPROVE))
        self.assertTrue(AP.needs_user(AP.L4_RESTRICTED))


class ChainTests(unittest.TestCase):
    def test_safe_finding_clears_internal_chain_without_user(self) -> None:
        f = AP.RepoFinding("forgekit", "docs 보강 필요", kind="docs")
        pkt, route, decision, trace = AP.run_internal_chain(f, risk_class="safe")
        self.assertEqual(decision.decision_class, "safe")
        self.assertEqual(decision.approval_level, AP.L2_INTERNAL_APPROVE)
        self.assertTrue(decision.can_execute)            # executable w/o the user
        self.assertTrue(AP.can_specialist_execute(decision))
        # the chain phases are recorded (PM → gateway → tech-lead)
        self.assertEqual(len(trace), 3)
        self.assertTrue(pkt.user_value)                  # PM framed user value
        self.assertTrue(route.owner_role)

    def test_risky_finding_needs_user_not_executable(self) -> None:
        f = AP.RepoFinding("bkurs-be", "auth 대규모 rewrite", kind="gap")
        _, _, decision, _ = AP.run_internal_chain(f, risk_class="risky")
        self.assertEqual(decision.decision_class, "risky")
        self.assertFalse(decision.can_execute)
        self.assertFalse(AP.can_specialist_execute(decision))  # autopilot stops at propose

    def test_restricted_finding_never_auto(self) -> None:
        f = AP.RepoFinding("bkurs-be", "프로덕션 배포 apply", kind="ops")
        _, _, decision, _ = AP.run_internal_chain(f, risk_class="blocked")
        self.assertEqual(decision.approval_level, AP.L4_RESTRICTED)
        self.assertFalse(AP.can_specialist_execute(decision))

    def test_no_decision_means_no_execution(self) -> None:
        # the hard rule: without a TechLeadDecision, a specialist may not execute.
        self.assertFalse(AP.can_specialist_execute(None))

    def test_trace_note_records_who_why_chain(self) -> None:
        f = AP.RepoFinding("forgekit", "lint 정리", kind="lint")
        _, _, decision, trace = AP.run_internal_chain(f, risk_class="safe")
        note = AP.trace_note("be", decision, what="lint 자동 정리", trace=trace)
        self.assertEqual(note.who, "be")
        self.assertTrue(note.why)
        self.assertIn("signoff:tech-lead", note.approval_chain)


if __name__ == "__main__":
    unittest.main()
