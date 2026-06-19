"""WT3 — always-on daemon operator surface: the TUI `/daemon` reflects the REAL
heartbeat/kill-switch the bounded daemon writes, and `/daemon stop` actually halts a
running serve loop.

Pure + stdlib (tempdir FORGEKIT_HOME, injected no-op sleep) → bare CI install. Proves
the surface is wired to the real daemon, not a stub: a real BoundedDaemon.serve writes
the heartbeat the surface reads, and the kill-switch the surface sets stops serve.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_console.runtime import heartbeat as hb
from forgekit_console.runtime import surface as rs
from forgekit_console.runtime.daemon import BoundedDaemon, TickOutcome


class DaemonSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.home)}

    def _ctx(self):
        return ConsoleContext(repo_root=Path("."), agents=load_agents(),
                              commands=load_commands(), env=self.env)

    def _run(self, cmd):
        return list(route(parse_input(cmd), self._ctx()).lines)

    # --- pure surface -------------------------------------------------------
    def test_stopped_when_no_heartbeat(self) -> None:
        self.assertEqual(rs.daemon_state(env=self.env), "stopped")
        lines = "\n".join(rs.daemon_status_lines(env=self.env))
        self.assertIn("stopped", lines)
        self.assertIn("runtime serve", lines)   # start hint

    def test_alive_when_running_heartbeat(self) -> None:
        hb.write_heartbeat(hb.Heartbeat(status=hb.STATUS_RUNNING, tick=5, ts="2026-06-19T10:00:00",
                                        pid=999, note="tick 5: observed"), env=self.env)
        self.assertEqual(rs.daemon_state(env=self.env), "alive")
        lines = "\n".join(rs.daemon_status_lines(env=self.env))
        self.assertIn("alive", lines)
        self.assertIn("tick 5", lines)
        self.assertIn("999", lines)             # pid surfaced

    def test_kill_pending_state(self) -> None:
        hb.write_heartbeat(hb.Heartbeat(status=hb.STATUS_RUNNING, tick=1), env=self.env)
        hb.request_kill(env=self.env)
        self.assertEqual(rs.daemon_state(env=self.env), "kill-pending")

    # --- router wiring ------------------------------------------------------
    def test_daemon_command_surfaces_heartbeat(self) -> None:
        hb.write_heartbeat(hb.Heartbeat(status=hb.STATUS_RUNNING, tick=7, pid=321), env=self.env)
        lines = "\n".join(self._run("/daemon"))
        self.assertIn("alive", lines)
        self.assertIn("tick 7", lines)

    def test_daemon_stop_sets_kill_switch(self) -> None:
        msg = self._run("/daemon stop")[0]
        self.assertIn("kill-switch", msg.lower())
        self.assertTrue(hb.is_killed(env=self.env))   # really wrote the kill file

    # --- end-to-end with the REAL bounded daemon ----------------------------
    def test_real_serve_writes_heartbeat_the_surface_reads(self) -> None:
        d = BoundedDaemon(poll_interval=0, max_ticks=3, env=self.env, pid=4242,
                          sleep_fn=lambda s: None)
        res = d.serve(lambda t: TickOutcome(summary=f"tick {t}", executed=0, waiting=False))
        self.assertEqual(res.ticks, 3)
        self.assertEqual(res.heartbeats, 3)
        lines = "\n".join(self._run("/daemon"))
        self.assertIn("tick 3", lines)          # the surface reads the daemon's real beat
        self.assertIn("4242", lines)

    def test_kill_switch_halts_serve(self) -> None:
        # operator sets the kill-switch (via the surface) → a serve loop exits at once.
        rs.request_stop(env=self.env)
        d = BoundedDaemon(poll_interval=0, max_ticks=10, env=self.env, sleep_fn=lambda s: None)
        res = d.serve(lambda t: TickOutcome(summary="x", executed=0, waiting=False))
        self.assertEqual(res.ticks, 0)
        self.assertEqual(res.stopped_reason, "kill switch")


if __name__ == "__main__":
    unittest.main()
