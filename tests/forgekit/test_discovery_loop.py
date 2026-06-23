"""Discovery bounded loop + promotion criteria + evidence track.

Proves the wave's three new seams on top of the ledger:

  * **bounded 24h loop** — repeated sweeps across an INJECTED clock accumulate into the
    ledger (dedup), and the loop stops honestly (window / max-ticks / cadence), never
    sleeping or faking time;
  * **promotion criteria** — only ideas corroborated across ≥N sweeps, scored, and still
    fresh become "ask the operator" candidates (single-sweep noise is excluded);
  * **evidence track** — a sweep's competitor/gap map + self-improve signals author into
    vault evidence notes (raw 00-inbox, never hollow), beyond the idea-brief note.

Network-free + deterministic: empty collector queries → repo-local only, fake fetcher,
isolated FORGEKIT_HOME (ledger state dir) and a throwaway vault.
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


# drop HN/Reddit/GitHub → repo-local only → deterministic, no network
_OFFLINE_CFG = {"discovery": {"hackernews_query": "", "subreddits": [], "github_query": ""}}


def _rmtree(p: Path) -> None:
    __import__("shutil").rmtree(p, ignore_errors=True)


class DiscoveryLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(self.tmp))
        self.addCleanup(lambda: _rmtree(self.home))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "m.py").write_text(
            "# TODO a\n# TODO b\n# FIXME c\n", encoding="utf-8")
        self.env = {"FORGEKIT_HOME": str(self.home)}

    def _loop(self, clock, *, budget=None, ledger=None, extra=()):
        kw = {"clock": clock, "config": _OFFLINE_CFG, "fetcher": _empty_fetcher,
              "extra_signals": extra}
        if budget is not None:
            kw["budget"] = budget
        if ledger is not None:
            kw["ledger"] = ledger
        return D.run_discovery_loop(self.tmp, **kw)

    # --- bounded loop accumulation -------------------------------------------
    def test_loop_accumulates_and_dedups_across_ticks(self) -> None:
        clock = [f"2026-06-23T{h:02d}:00:00" for h in range(0, 8, 2)]  # 4 ticks, 2h apart
        rep = self._loop(clock)
        self.assertEqual(len(rep.ticks), 4)
        self.assertGreaterEqual(rep.new_total, 1)          # first tick introduces ideas
        self.assertGreaterEqual(rep.seen_total, 1)         # later ticks re-see (dedup)
        # only the FIRST tick should add new ideas (same repo-local source every tick)
        self.assertEqual(rep.ticks[0].new_count, rep.new_total)
        self.assertTrue(all(t.new_count == 0 for t in rep.ticks[1:]))
        self.assertEqual(rep.stopped_reason, "clock-exhausted")

    def test_loop_stops_at_window(self) -> None:
        # ticks span 30h but the window is 24h → stop once past the window
        clock = [f"2026-06-{23 + (h // 24):02d}T{h % 24:02d}:00:00" for h in range(0, 31, 3)]
        rep = self._loop(clock, budget=D.LoopBudget(window_hours=24.0, min_interval_minutes=0))
        self.assertEqual(rep.stopped_reason, "window-exhausted")
        # every recorded tick is within 24h of the start
        self.assertTrue(all(D.age_hours(t.at, rep.started_at) <= 24.0 for t in rep.ticks))

    def test_loop_stops_at_max_ticks(self) -> None:
        clock = [f"2026-06-23T{h:02d}:00:00" for h in range(0, 10)]  # 10 candidate ticks
        rep = self._loop(clock, budget=D.LoopBudget(max_ticks=3, min_interval_minutes=0))
        self.assertEqual(len(rep.ticks), 3)
        self.assertEqual(rep.stopped_reason, "max-ticks")

    def test_loop_respects_min_interval(self) -> None:
        # timestamps 10 min apart but min interval is 30 min → only the first ticks
        clock = [f"2026-06-23T00:{m:02d}:00" for m in range(0, 50, 10)]
        rep = self._loop(clock, budget=D.LoopBudget(min_interval_minutes=30))
        # 00:00 (tick), 00:10 skip, 00:20 skip, 00:30 (tick), 00:40 skip → 2 ticks
        self.assertEqual(len(rep.ticks), 2)

    def test_loop_persists_when_env_given(self) -> None:
        clock = ["2026-06-23T00:00:00", "2026-06-23T02:00:00"]
        led = D.DiscoveryLedger()
        D.run_discovery_loop(self.tmp, clock=clock, config=_OFFLINE_CFG,
                             fetcher=_empty_fetcher, ledger=led, persist_env=self.env)
        reloaded = D.DiscoveryLedger.load(self.env)
        self.assertEqual(set(reloaded.ideas), set(led.ideas))   # survived persist

    # --- promotion criteria (ask-me-later candidates) ------------------------
    def test_single_sweep_idea_is_not_yet_a_candidate(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-23T00:00:00")
        # seen once → corroboration threshold (≥2) not met → no candidate
        self.assertEqual(D.ask_candidates(led, "2026-06-23T00:00:00"), [])

    def test_corroborated_fresh_idea_becomes_candidate(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-23T00:00:00")
        led.record_sweep(self._sweep(), now="2026-06-23T01:00:00")   # seen twice
        cands = D.ask_candidates(led, "2026-06-23T01:30:00")
        self.assertTrue(cands)
        idea, reason = cands[0]
        self.assertGreaterEqual(idea.seen_count, 2)
        self.assertIn("교차 관측", reason)

    def test_stale_idea_excluded_from_candidates(self) -> None:
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep(), now="2026-06-23T00:00:00")
        led.record_sweep(self._sweep(), now="2026-06-23T01:00:00")
        # 47h after last_seen → past the 36h freshness window → excluded
        self.assertEqual(D.ask_candidates(led, "2026-06-25T00:00:00"), [])
        self.assertTrue(D.stale_pending(led, "2026-06-25T00:00:00"))

    def test_freshness_helpers(self) -> None:
        self.assertAlmostEqual(D.age_hours("2026-06-23T05:00:00", "2026-06-23T00:00:00"), 5.0)
        self.assertIsNone(D.age_hours("not-a-date", "2026-06-23T00:00:00"))  # honest unknown

    # --- evidence track (gap + self-improve → vault notes) -------------------
    def _sweep(self, extra=("경쟁 제품 X 가 이미 있다", "forgekit 콘솔 자체가 느림")):
        return D.run_discovery_sweep(self.tmp, fetcher=_empty_fetcher, config=_OFFLINE_CFG,
                                     extra_signals=list(extra))

    def test_persist_evidence_writes_gap_and_self_improve(self) -> None:
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        paths = D.persist_evidence(self._sweep(), vault)
        self.assertIsNotNone(paths["gap"])
        self.assertIsNotNone(paths["self_improve"])
        gap_text = Path(paths["gap"]).read_text(encoding="utf-8")
        self.assertIn("경쟁 지형", gap_text)
        self.assertIn("competitor-gap", gap_text)            # tag present (retrieval-friendly)
        si_text = Path(paths["self_improve"]).read_text(encoding="utf-8")
        self.assertIn("forgekit 콘솔 자체가 느림", si_text)

    def test_persist_evidence_is_honest_when_empty(self) -> None:
        empty_repo = Path(tempfile.mkdtemp())
        (empty_repo / "apps").mkdir()                         # no TODO/large files → no gaps
        self.addCleanup(lambda: _rmtree(empty_repo))
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        sweep = D.run_discovery_sweep(empty_repo, fetcher=_empty_fetcher, config=_OFFLINE_CFG)
        paths = D.persist_evidence(sweep, vault)
        self.assertIsNone(paths["gap"])                      # nothing observed → no hollow note
        self.assertIsNone(paths["self_improve"])

    def test_persist_evidence_no_vault_returns_none(self) -> None:
        paths = D.persist_evidence(self._sweep(), None)
        self.assertEqual(paths, {"gap": None, "self_improve": None})

    # --- router surfaces ------------------------------------------------------
    def _ctx(self, *, config=None):
        cfg = dict(_OFFLINE_CFG)
        if config:
            cfg.update(config)
        return ConsoleContext(repo_root=self.tmp, env=self.env, config=cfg)

    def test_router_candidates_surfaces_after_corroboration(self) -> None:
        ctx = self._ctx()
        route(parse_input("/discovery"), ctx)                # sweep 1
        first = route(parse_input("/discovery candidates"), ctx)
        self.assertIn("후보 없음", "\n".join(first.lines))    # seen once → none yet
        route(parse_input("/discovery"), ctx)                # sweep 2 → corroborated
        second = route(parse_input("/discovery candidates"), ctx)
        self.assertIn("물어볼 후보", "\n".join(second.lines))
        self.assertIn("교차 관측", "\n".join(second.lines))

    def test_router_evidence_connected_persists(self) -> None:
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        ctx = self._ctx(config={"nexus_root": str(vault)})
        res = route(parse_input("/discovery evidence"), ctx)
        self.assertEqual(res.kind, "info")
        self.assertIn("evidence note 기록", "\n".join(res.lines))

    def test_router_evidence_not_connected_is_honest(self) -> None:
        res = route(parse_input("/discovery evidence"), self._ctx())  # no nexus_root
        self.assertEqual(res.kind, "error")
        self.assertIn("미연결", "\n".join(res.lines))


if __name__ == "__main__":
    unittest.main()
