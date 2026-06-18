"""Discovery program end-to-end (WT6) — the new modes chain coherently, no fakes.

sources (free-first, planned never fake) → idea-discovery (briefs + self-improve split)
→ promote to PM handoff → self-improvement packets (risk-classed, bounded) → red/blue
(own-asset plan-only, public blocked). Asserts the chain closes + every safety gate
holds. Pure/offline → bare CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console import selfimprove as SI
from forgekit_console import security as SEC
from forgekit_console import sources as SRC
from forgekit_console.security import contract as SC


class DiscoveryEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "m.py").write_text("# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")

    def test_full_discovery_chain_closes(self) -> None:
        # 1) sources — free-first live; planned never fake (WT2)
        reg = SRC.default_registry(self.tmp, fetcher=lambda u: "{}")
        self.assertEqual(reg.cost_ordered_live()[0].spec.cost_class, SRC.COST_FREE)
        self.assertTrue(all(c.collect() == [] for c in reg.planned()))   # planned: no fake

        # 2) idea-discovery → briefs + self-improve split (WT3)
        signals = ["동기화가 느려서 불편", "AI 메모 트렌드 급상승", "forgekit 콘솔 자체 개선 필요"]
        disc = D.run_idea_discovery(signals)
        self.assertTrue(disc.idea_briefs)
        self.assertTrue(disc.self_improve_signals)

        # 3) promote a brief to a real PM handoff (WT2 reuse)
        ho = D.promote_to_handoff(disc.top_brief)
        self.assertEqual(ho.trace[-1].phase, "tech-lead")

        # 4) self-improvement: repo scan + discovery self-improve signals → packets (WT4)
        si = SI.run_self_improvement(self.tmp, signals=disc.self_improve_signals, limit=10)
        self.assertTrue(si.packets)
        self.assertTrue(si.safe)                       # TODO cleanup is safe
        # no packet is auto-executable beyond safe; blocked never auto
        self.assertTrue(all(p.approval_needed for p in si.risky + si.blocked))

        # 5) red/blue: own asset plan-only, public blocked (WT5)
        own = SEC.build_drill("k3s-isolated")
        public = SEC.build_drill("example.com")
        self.assertEqual(own.status, SC.DRILL_PLAN_ONLY)
        self.assertFalse(own.executed)
        self.assertEqual(public.status, SC.DRILL_BLOCKED)

    def test_no_fake_or_auto_execution_anywhere(self) -> None:
        # video-watch link-only is honest reference_only (no crawl)
        vw = D.summarize_ingest(D.VideoIngest(link="https://youtube.com/x"))
        self.assertEqual(vw.status, "reference_only")
        # red/blue never auto-executes from the default (unapproved) path
        self.assertFalse(SEC.build_drill("k3s-isolated").executed)
        # self-improvement blocked class routes to runbook, not execution
        blk = SI.make_packet("프로덕션 배포 apply", area="deploy")
        self.assertIn("자동 실행 금지", SI.route_packet(blk))


if __name__ == "__main__":
    unittest.main()
