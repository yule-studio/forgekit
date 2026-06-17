"""Bounded always-on runtime loop (WT3) — bounded autonomy, no fake execution.

Proves: the loop is bounded (max_iterations), NEVER executes privileged work (it
produces a runbook + waits for the operator), observe-only mode only reports, and a
product gap flows observe→classify→packet→handoff→wait. Pure + deterministic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.runtime import loop as L
from forgekit_console.runtime import runbook as RB
from forgekit_console.lifecycle.failure_escalation import FailureEscalator


class RunbookTests(unittest.TestCase):
    def test_areas_classified_and_markdown_built(self) -> None:
        self.assertEqual(RB.infer_area("프로덕션 배포 rollout"), RB.AREA_DEPLOY)
        self.assertEqual(RB.infer_area("IAM 역할 권한 부여"), RB.AREA_IAM)
        self.assertEqual(RB.infer_area("secret 키 회전"), RB.AREA_SECRET)
        note = RB.build_runbook(RB.AREA_DEPLOY, title="배포", context="bkurs")
        md = note.to_markdown()
        self.assertIn("# Runbook", md)
        self.assertIn("Terraform", md)
        self.assertIn("승인", md)


class BoundedLoopTests(unittest.TestCase):
    def test_privileged_finding_makes_runbook_not_execution(self) -> None:
        loop = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_BOUNDED, max_iterations=5)
        findings = [L.Finding("bkurs", "프로덕션 배포 apply", category=L.CAT_INFRA, privileged=True)]
        res = loop.run(findings)
        # produced a runbook + is waiting on the operator; NO execute phase exists
        self.assertEqual(res.blocked_count, 1)
        self.assertTrue(res.waiting)
        phases = {s.phase for s in res.steps}
        self.assertIn(L.PHASE_RUNBOOK, phases)
        self.assertNotIn("execute", phases)

    def test_product_gap_flows_through_packet_handoff(self) -> None:
        loop = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_BOUNDED, max_iterations=5)
        res = loop.run([L.Finding("bkurs", "영상 업로드 기능 미완성", category=L.CAT_PRODUCT)])
        phases = [s.phase for s in res.steps]
        self.assertIn(L.PHASE_OBSERVE, phases)
        self.assertIn(L.PHASE_CLASSIFY, phases)
        self.assertIn(L.PHASE_PACKET, phases)
        self.assertIn(L.PHASE_HANDOFF, phases)
        self.assertIn(L.PHASE_WAIT, phases)
        self.assertEqual(len(res.handoffs), 1)

    def test_observe_only_mode_does_not_packet(self) -> None:
        loop = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_OBSERVE, max_iterations=5)
        res = loop.run([L.Finding("bkurs", "디자인 간격 부족", category=L.CAT_DESIGN)])
        phases = {s.phase for s in res.steps}
        self.assertNotIn(L.PHASE_PACKET, phases)
        self.assertNotIn(L.PHASE_HANDOFF, phases)
        self.assertEqual(len(res.handoffs), 0)

    def test_loop_is_bounded_by_max_iterations(self) -> None:
        loop = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_BOUNDED, max_iterations=2)
        findings = [L.Finding("p", f"gap {i}", category=L.CAT_PRODUCT) for i in range(10)]
        res = loop.run(findings)
        iters = {s.iteration for s in res.steps}
        self.assertLessEqual(max(iters), 2)  # never ran past the bound
        self.assertIn("max_iterations", res.halt_reason)

    def test_waiting_loop_escalates_to_operator(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        esc = FailureEscalator(env={}, threshold=1, ledger_path=tmp / "l.json",
                               inbox_path=tmp / "i.json", notifier=lambda t, b: True,
                               bridge_troubleshooting=False)
        loop = L.BoundedRuntimeLoop(autonomy=L.AUTONOMY_BOUNDED, max_iterations=5, escalator=esc)
        res = loop.run([L.Finding("bkurs", "infra apply", category=L.CAT_INFRA, privileged=True)])
        self.assertTrue(res.waiting)
        # the parked-on-operator state surfaced to the escalation inbox (not silent)
        self.assertTrue((tmp / "i.json").exists())


if __name__ == "__main__":
    unittest.main()
