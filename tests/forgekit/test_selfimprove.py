"""Self-improvement (WT4) — repo gaps → risk-classified packets, bounded (no exec).

Proves: scan produces improvement packets with user-discomfort framing, risk class
splits safe/risky/blocked, only SAFE is auto-OK (within approval), and blocked
(deploy/secret/infra) is never auto. Pure/offline → bare CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import selfimprove as SI
from forgekit_console.selfimprove import packet as PK


class RiskClassTests(unittest.TestCase):
    def test_classify_safe_risky_blocked(self) -> None:
        self.assertEqual(PK.classify_risk("docs 보강"), PK.RISK_SAFE)
        self.assertEqual(PK.classify_risk("auth 권한 대규모 rewrite"), PK.RISK_RISKY)
        self.assertEqual(PK.classify_risk("프로덕션 배포 apply"), PK.RISK_BLOCKED)
        self.assertEqual(PK.classify_risk("secret 회전"), PK.RISK_BLOCKED)

    def test_only_safe_is_auto_ok(self) -> None:
        self.assertFalse(PK.make_packet("docs 보강").approval_needed)        # safe → auto-OK
        self.assertTrue(PK.make_packet("배포 apply").approval_needed)        # blocked → gated
        self.assertTrue(PK.make_packet("대규모 rewrite").approval_needed)    # risky → gated


class ScanTests(unittest.TestCase):
    def test_scan_produces_packets_with_discomfort(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "apps").mkdir()
        (tmp / "apps" / "x.py").write_text("# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")
        res = SI.run_self_improvement(tmp, limit=10)
        self.assertTrue(res.packets)
        self.assertTrue(all(p.user_discomfort for p in res.packets))  # user-value framing
        self.assertTrue(res.safe)                                     # TODO cleanup = safe

    def test_self_improve_signals_become_packets(self) -> None:
        from forgekit_console.discovery.models import OpportunitySignal, SIGNAL_SELF_IMPROVE

        sig = OpportunitySignal("forgekit 콘솔 도움말 부족", kind=SIGNAL_SELF_IMPROVE)
        res = SI.run_self_improvement("/tmp/none", signals=[sig], limit=10)
        self.assertTrue(any(p.source_origin == "idea-discovery" for p in res.packets))

    def test_routing_never_executes_blocked(self) -> None:
        blocked = PK.make_packet("프로덕션 배포 apply", area="deploy")
        self.assertEqual(blocked.risk, PK.RISK_BLOCKED)
        self.assertIn("자동 실행 금지", SI.route_packet(blocked))
        safe = PK.make_packet("docs 보강")
        self.assertIn("safe", SI.route_packet(safe))


if __name__ == "__main__":
    unittest.main()
