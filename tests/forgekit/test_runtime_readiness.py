"""Runtime readiness join (issue #428) — daemon × goal continuity × live transport.

Proves the ONE honest "can the always-on loop make progress?" verdict:
- the verdict names the binding constraint: setup_required / idle_no_goals / awaiting_operator
  / no_live_lane / progressing — derived from the REAL heartbeat, goal store, and provider config;
- **no fake-live**: an unprobed live-capable slot is "declared (미검증)", never asserted live;
  a probe (``live_map``) upgrades it to verified, a False probe downgrades it honestly;
- the `/daemon` surface and `/setup` bootstrap both carry the readiness block;
- unattended is platform-honest (macOS clamshell caveat vs Linux/systemd).

Pure / stdlib (tempdir FORGEKIT_HOME) → bare CI install.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_runtime.runtime import readiness as R
from forgekit_goal import Goal, GoalStatus, GoalStore

_FOUR = {"primary_provider": "claude",
         "linked_providers": ["claude", "codex", "gemini", "ollama"],
         "slot_routing": {"default_chat": "gemini", "execution": "codex", "research": "gemini"}}
_NO_LIVE = {"primary_provider": "claude", "linked_providers": ["claude", "codex"],
            "slot_routing": {"default_chat": "claude", "execution": "codex", "research": "claude"}}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.home)}
        self.store = GoalStore(env=self.env)

    def _goal(self, status):
        self.store.save(Goal(id="g1", title="big goal", intent="x", status=status))

    def _assess(self, **kw):
        return R.assess_runtime_readiness(env=self.env, store=self.store, **kw)


class VerdictTests(_Base):
    def test_setup_required_without_provider(self):
        r = self._assess(config={})
        self.assertEqual(r.verdict, R.RD_SETUP_REQUIRED)
        self.assertFalse(r.provider_configured)

    def test_idle_no_goals(self):
        r = self._assess(config=_FOUR)
        self.assertEqual(r.verdict, R.RD_IDLE_NO_GOALS)
        self.assertTrue(r.has_live_lane)

    def test_progressing_with_active_goal_and_live_lane(self):
        self._goal(GoalStatus.ACTIVE)
        r = self._assess(config=_FOUR)
        self.assertEqual(r.verdict, R.RD_PROGRESSING)

    def test_no_live_lane_with_active_goal_but_cli_only(self):
        self._goal(GoalStatus.ACTIVE)
        r = self._assess(config=_NO_LIVE)
        self.assertEqual(r.verdict, R.RD_NO_LIVE_LANE)
        self.assertFalse(r.has_live_lane)

    def test_awaiting_operator_takes_priority(self):
        self._goal(GoalStatus.AWAITING_APPROVAL)
        r = self._assess(config=_FOUR)
        self.assertEqual(r.verdict, R.RD_AWAITING_OPERATOR)
        self.assertEqual(r.awaiting_approval, 1)


class NoFakeLiveTests(_Base):
    def test_unprobed_slot_is_capable_not_verified(self):
        r = self._assess(config=_FOUR)   # no live_map
        self.assertTrue(r.has_live_lane)          # capable
        self.assertFalse(r.live_verified)         # but NOT asserted verified
        gem = next(s for s in r.slots if s.actual == "gemini")
        self.assertIsNone(gem.verified)

    def test_probe_verifies_and_downgrades_honestly(self):
        r = self._assess(config=_FOUR, live_map={"gemini": True, "ollama": False})
        self.assertTrue(r.live_verified)
        gem = next(s for s in r.slots if s.actual == "gemini")
        self.assertIs(gem.verified, True)

    def test_cli_provider_is_routing_only_not_live(self):
        r = self._assess(config=_NO_LIVE)
        # claude (CLI) declared on default_chat → routing-only, never live-capable.
        chat = next(s for s in r.slots if s.slot == "default_chat")
        self.assertFalse(chat.live_capable)


class SurfaceTests(_Base):
    def test_readiness_lines_render_verdict_and_transport(self):
        self._goal(GoalStatus.ACTIVE)
        text = "\n".join(R.readiness_lines(env=self.env, config=_FOUR, store=self.store))
        self.assertIn("readiness", text)
        self.assertIn("progressing", text)
        self.assertIn("transport", text)

    def test_daemon_surface_includes_readiness(self):
        from forgekit_runtime.runtime import surface as rs
        # daemon stopped, no provider — surface must still render a readiness verdict honestly.
        text = "\n".join(rs.daemon_status_lines(env=self.env))
        self.assertIn("readiness", text)

    def test_unattended_note_is_platform_specific(self):
        mac = self._assess(config=_FOUR, platform="darwin").unattended_note
        lin = self._assess(config=_FOUR, platform="linux").unattended_note
        self.assertIn("clamshell", mac)
        self.assertIn("linger", lin)
        self.assertNotEqual(mac, lin)

    def test_next_action_leads_with_serve_when_stopped(self):
        self._goal(GoalStatus.ACTIVE)
        r = self._assess(config=_FOUR)
        self.assertEqual(r.daemon_state, "stopped")
        self.assertIn("serve", r.next_action)

    def test_serialisable(self):
        d = self._assess(config=_FOUR).to_dict()
        self.assertIn("verdict", d)
        self.assertIn("slots", d)
        self.assertIn("has_live_lane", d)


class SetupBootstrapTests(_Base):
    def test_setup_bootstrap_carries_readiness_block(self):
        from forgekit_console import bootstrap as bs

        class _P:  # deterministic probe — nothing live
            def cli_authenticated(self, p): return True
            def api_key(self, p, env=None): return ""
            def daemon_reachable(self, e): return False
            def installed_models(self, e): return ()

        text = "\n".join(bs.bootstrap_lines(_FOUR, env=self.env, probe=_P()))
        self.assertIn("runtime readiness", text)


if __name__ == "__main__":
    unittest.main()
