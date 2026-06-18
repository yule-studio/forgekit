"""Daemon ↔ autopilot execution wiring (WT2 #241).

Proves the always-on tick actually EXECUTES bounded safe-class mutations (not observe-
only): a safe finding writes a real file once, dedupe stops re-running it, risky/
restricted findings stay surfaced (never executed), and repeated verify failures trip
a cooldown. Also drives the real BoundedDaemon to show execution metadata flows out.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.autopilot.runner import BoundedMutator, ExecOutcome
from forgekit_console.runtime.autopilot_tick import AutopilotTicker
from forgekit_console.runtime.daemon import BoundedDaemon


class _Item:
    def __init__(self, title):
        self.title = title


class FakeCollector:
    def __init__(self, titles):
        self.titles = list(titles)

    def collect(self, limit=4):
        return [_Item(t) for t in self.titles[:limit]]


class FailingMutator:
    """Always fails verify → drives the failure-threshold / cooldown path."""

    def execute(self, task):
        return ExecOutcome(False, action=task.action, path=task.rel_path,
                           refused_reason="verify fail (test)")


class TickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _ticker(self, titles, **kw):
        return AutopilotTicker(repo_root=self.tmp, collector=FakeCollector(titles), **kw)

    def test_safe_class_executes_real_file_once(self) -> None:
        t = self._ticker(["문서 보강 필요"])  # clean wording → safe (L2)
        out = t.tick(1)
        self.assertGreaterEqual(out.executed, 1)
        self.assertTrue(out.executed_paths)
        # the mutation is REAL — the file exists under runs/ and has content
        written = self.tmp / out.executed_paths[0]
        self.assertTrue(written.exists())
        self.assertIn("autopilot note", written.read_text(encoding="utf-8"))

    def test_dedupe_stops_reexecution(self) -> None:
        t = self._ticker(["문서 보강 필요"])
        first = t.tick(1)
        self.assertGreaterEqual(first.executed, 1)
        second = t.tick(2)              # same finding → deduped, not re-run (no churn)
        self.assertEqual(second.executed, 0)
        self.assertIn("dupes", second.skipped_reason)

    def test_restricted_probe_surfaces_never_executes(self) -> None:
        # the standing "운영/배포 준비 점검" finding is L4 → proposed, waiting, never executed
        t = self._ticker([])           # only the restricted probe remains
        out = t.tick(1)
        self.assertEqual(out.executed, 0)
        self.assertTrue(out.waiting)
        self.assertGreaterEqual(out.blocked_count, 1)

    def test_repeated_failure_trips_cooldown(self) -> None:
        t = self._ticker(["alpha 수정", "beta 수정", "gamma 수정"],
                         mutator=FailingMutator(), cooldown_ticks=2)
        first = t.tick(1)              # 3 safe findings all fail verify → halt
        self.assertEqual(first.executed, 0)
        self.assertTrue(first.waiting)
        self.assertEqual(first.next_eligible_tick, 3)
        cooled = t.tick(2)            # within cooldown → skipped, no execution attempt
        self.assertEqual(cooled.skipped_reason, "cooldown")
        self.assertTrue(cooled.waiting)

    def test_serve_flows_execution_metadata(self) -> None:
        hb = self.tmp / "hb.json"
        kill = self.tmp / "kill"
        ticker = AutopilotTicker(repo_root=self.tmp, collector=FakeCollector(["리포트 정리"]),
                                 mutator=BoundedMutator(self.tmp))
        daemon = BoundedDaemon(poll_interval=0.0, max_ticks=2, heartbeat_path=hb,
                               kill_switch_path=kill, sleep_fn=lambda s: None, pid=7)
        res = daemon.serve(ticker.tick_fn())
        self.assertEqual(res.ticks, 2)
        self.assertGreaterEqual(res.executed, 1)        # tick1 executes, tick2 dedupes

    def test_once_heartbeat_surfaces_tick_summary(self) -> None:
        from forgekit_console.runtime import heartbeat as HB

        hb = self.tmp / "hb.json"
        ticker = AutopilotTicker(repo_root=self.tmp, collector=FakeCollector(["리포트 정리"]),
                                 mutator=BoundedMutator(self.tmp))
        daemon = BoundedDaemon(heartbeat_path=hb, kill_switch_path=self.tmp / "k",
                               sleep_fn=lambda s: None, pid=7)
        out = daemon.once(ticker.tick_fn())
        self.assertGreaterEqual(out.executed, 1)
        # `forgekit runtime status` reads this heartbeat note → surfaces what the tick did
        self.assertIn("exec", HB.read_heartbeat(path=hb).note)


if __name__ == "__main__":
    unittest.main()
