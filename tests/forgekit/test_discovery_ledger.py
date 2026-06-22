"""Discovery ledger — ideas ACCUMULATE across sweeps (dedup + lifecycle + persist).

Proves the 누적 seam: a sweep records ideas into a persisted, deduplicated ledger so
re-running `/discovery` does NOT resurface the same idea as new; each idea carries a
lifecycle status (new → seen → promoted/saved/parked) and accumulation evidence
(seen_count). Also covers operator-tunable collectors (registry_from_config) and the
ledger-backed `/discovery` surface via the pure router with an isolated, NETWORK-FREE
context (empty collector queries → repo-local only → deterministic, no network in CI).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route


def _empty_fetcher(_url: str) -> str:
    return "{}"


# network-free config: drop HN/Reddit/GitHub so only repo-local runs → deterministic
_OFFLINE_CFG = {"discovery": {"hackernews_query": "", "subreddits": [], "github_query": ""}}


class DiscoveryLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "m.py").write_text(
            "# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")
        self.env = {"FORGEKIT_HOME": str(self.home)}

    def _sweep(self):
        return D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher, config=_OFFLINE_CFG,
                                     extra_signals=["노트 동기화 느려서 불편"])

    # --- accumulation / dedup -------------------------------------------------
    def test_resweep_dedups_and_bumps_seen_count(self) -> None:
        led = D.DiscoveryLedger()
        new1, upd1 = led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        self.assertTrue(new1)                      # first sweep → all new
        self.assertFalse(upd1)
        new2, upd2 = led.record_sweep(self._sweep(), now="2026-06-22T11:00:00")
        self.assertFalse(new2)                     # SAME ideas → none new (dedup)
        self.assertTrue(upd2)                      # they were re-seen
        self.assertTrue(all(i.seen_count == 2 for i in upd2))
        self.assertTrue(all(i.status == D.ST_SEEN for i in upd2))

    def test_persist_round_trip(self) -> None:
        led = D.DiscoveryLedger.load(self.env)
        led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        path = led.save(self.env)
        self.assertIsNotNone(path)
        again = D.DiscoveryLedger.load(self.env)
        self.assertEqual(set(again.ideas), set(led.ideas))   # survived a reload

    def test_lifecycle_decided_ideas_drop_from_pending(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        before = len(led.pending())
        self.assertGreaterEqual(before, 1)
        top = led.pending()[0]
        led.mark(top.fingerprint, D.ST_PROMOTED)
        self.assertEqual(len(led.pending()), before - 1)     # promoted → not pending
        # a later sweep re-seeing it must NOT resurface it as new/pending
        new, _ = led.record_sweep(self._sweep(), now="2026-06-22T12:00:00")
        self.assertNotIn(top.fingerprint, {i.fingerprint for i in new})
        self.assertEqual(led.ideas[top.fingerprint].status, D.ST_PROMOTED)

    def test_pending_is_score_ordered(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        scores = [i.score for i in led.pending()]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_rebuild_brief_enables_promotion_without_resweep(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        brief = led.pending()[0].rebuild_brief()
        ho = D.promote_brief(brief)
        self.assertEqual(ho.trace[-1].phase, "tech-lead")

    def test_summary_counts(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-22T10:00:00")
        led.mark(led.pending()[0].fingerprint, D.ST_PARKED)
        s = led.summary()
        self.assertEqual(s["parked"], 1)
        self.assertEqual(s["total"], len(led.ideas))

    # --- collector usability (operator-tunable topics) ------------------------
    def test_registry_from_config_applies_topics(self) -> None:
        from forgekit_console.sources import registry_from_config

        cfg = {"discovery": {"subreddits": ["SaaS", "startups"],
                             "hackernews_query": "AI agents", "github_query": ""}}
        reg = registry_from_config(self.tmp, cfg, fetcher=_empty_fetcher)
        ids = [c.spec.id for c in reg.live()]
        self.assertEqual(ids.count("reddit"), 2)        # two subreddits → two collectors
        self.assertIn("hackernews", ids)
        self.assertNotIn("github", ids)                 # empty query → dropped (no fake)

    def test_models_from_dict_round_trip(self) -> None:
        from forgekit_console.discovery import models as M

        b = M.IdeaBrief(title="t", problem="p",
                        differentiation=M.DifferentiationHypothesis("h", "r"),
                        next_experiment=M.NextExperiment("e", "m"), score=3.0)
        self.assertEqual(M.IdeaBrief.from_dict(b.to_dict()).to_dict(), b.to_dict())

    # --- ledger-backed /discovery surface (router, isolated) ------------------
    def _ctx(self, *, config=None):
        cfg = dict(_OFFLINE_CFG)
        if config:
            cfg.update(config)
        return ConsoleContext(repo_root=self.tmp, env=self.env, config=cfg)

    def test_router_digest_accumulates(self) -> None:
        ctx = self._ctx()
        first = route(parse_input("/discovery"), ctx)
        self.assertIn("누적 digest", "\n".join(first.lines))
        self.assertIn("새 아이디어", "\n".join(first.lines))
        # second run → dedup: 0 new (same repo-local source)
        second = route(parse_input("/discovery"), ctx)
        self.assertIn("새 아이디어 0건", "\n".join(second.lines))

    def test_router_pending_then_promote(self) -> None:
        ctx = self._ctx()
        route(parse_input("/discovery"), ctx)
        pend = route(parse_input("/discovery pending"), ctx)
        self.assertIn("결정 대기", "\n".join(pend.lines))
        promo = route(parse_input("/discovery promote 1"), ctx)
        self.assertIn("promoted", "\n".join(promo.lines))
        led = D.DiscoveryLedger.load(self.env)
        self.assertEqual(led.summary()["promoted"], 1)

    def test_router_save_connected_vault_persists(self) -> None:
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(vault, ignore_errors=True))
        ctx = self._ctx(config={"nexus_root": str(vault)})
        route(parse_input("/discovery"), ctx)
        res = route(parse_input("/discovery save 1"), ctx)
        self.assertEqual(res.kind, "info")
        self.assertIn("authored note 기록", "\n".join(res.lines))
        self.assertEqual(D.DiscoveryLedger.load(self.env).summary()["saved"], 1)

    def test_router_save_not_connected_is_honest(self) -> None:
        ctx = self._ctx()                              # no nexus_root
        route(parse_input("/discovery"), ctx)
        res = route(parse_input("/discovery save 1"), ctx)
        self.assertEqual(res.kind, "error")
        self.assertIn("미연결", "\n".join(res.lines))

    def test_router_park_removes_from_pending(self) -> None:
        ctx = self._ctx()
        route(parse_input("/discovery"), ctx)
        before = len(D.DiscoveryLedger.load(self.env).pending())
        route(parse_input("/discovery park 1"), ctx)
        self.assertEqual(len(D.DiscoveryLedger.load(self.env).pending()), before - 1)


if __name__ == "__main__":
    unittest.main()
