"""Bounded always-on daemon (WT4) — real loop, heartbeat, kill switch, notify. Pure.

Proves the daemon is a REAL bounded loop (not a sim): serve runs N ticks bounded by
max_ticks, writes a heartbeat each tick, stops on a kill-switch file, and notifies the
operator on an approval-needed tick. Injected sleep/paths/notifier → deterministic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.runtime import heartbeat as HB
from forgekit_console.runtime.daemon import BoundedDaemon, TickOutcome


class FakeNotifier:
    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)
        return None


class DaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.hb = self.tmp / "hb.json"
        self.kill = self.tmp / "kill"
        self.slept = []

    def _daemon(self, **kw):
        return BoundedDaemon(
            poll_interval=5.0, heartbeat_path=self.hb, kill_switch_path=self.kill,
            sleep_fn=lambda s: self.slept.append(s), pid=123, **kw)

    def test_serve_bounded_by_max_ticks_and_heartbeats(self) -> None:
        d = self._daemon(max_ticks=3)
        res = d.serve(lambda n: TickOutcome(summary=f"t{n}"))
        self.assertEqual(res.ticks, 3)
        self.assertIn("max_ticks", res.stopped_reason)
        self.assertEqual(res.heartbeats, 3)
        self.assertEqual(self.slept, [5.0, 5.0, 5.0])     # real sleep called per tick (injected)
        hb = HB.read_heartbeat(path=self.hb)
        self.assertEqual(hb.status, HB.STATUS_STOPPED)    # final heartbeat
        self.assertEqual(hb.tick, 3)

    def test_kill_switch_stops_loop(self) -> None:
        HB.request_kill(path=self.kill)                   # pre-set kill
        d = self._daemon(max_ticks=99)
        res = d.serve(lambda n: TickOutcome())
        self.assertEqual(res.ticks, 0)                    # stopped before any tick
        self.assertEqual(res.stopped_reason, "kill switch")

    def test_kill_mid_run_stops_next_tick(self) -> None:
        d = self._daemon(max_ticks=99)

        def tick(n):
            if n == 2:
                HB.request_kill(path=self.kill)           # operator stops it during run
            return TickOutcome(summary=f"t{n}")

        res = d.serve(tick)
        self.assertEqual(res.ticks, 2)                    # ran 2, then kill caught next loop
        self.assertEqual(res.stopped_reason, "kill switch")

    def test_waiting_tick_notifies_operator(self) -> None:
        notof = FakeNotifier()
        d = self._daemon(max_ticks=2, notifier=notof)
        res = d.serve(lambda n: TickOutcome(waiting=True, blocked_count=1))
        self.assertEqual(res.waits, 2)
        self.assertEqual(res.notified, 2)
        self.assertEqual(len(notof.events), 2)
        self.assertEqual(notof.events[0].event_type, "APPROVAL_REQUIRED")

    def test_once_single_tick(self) -> None:
        d = self._daemon()
        out = d.once(lambda n: TickOutcome(summary="single", waiting=False))
        self.assertEqual(out.summary, "single")
        hb = HB.read_heartbeat(path=self.hb)
        self.assertEqual(hb.tick, 1)
        self.assertEqual(hb.status, HB.STATUS_IDLE)


class HeartbeatTests(unittest.TestCase):
    def test_roundtrip_and_kill(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        p = tmp / "hb.json"
        HB.write_heartbeat(HB.Heartbeat(HB.STATUS_RUNNING, 5, "2026-06-18T00:00:00", 1), path=p)
        hb = HB.read_heartbeat(path=p)
        self.assertTrue(hb.alive)
        self.assertEqual(hb.tick, 5)
        k = tmp / "kill"
        self.assertFalse(HB.is_killed(path=k))
        HB.request_kill(path=k)
        self.assertTrue(HB.is_killed(path=k))
        HB.clear_kill(path=k)
        self.assertFalse(HB.is_killed(path=k))


if __name__ == "__main__":
    unittest.main()
