"""Provider/runtime continuity + routing (lane) — brain/transport/fallback, per-tick evidence.

Locks the lane: each tick resolves an honest provider lane (live gemini/ollama vs
participant-only claude/codex vs fallback), records a durable per-tick ledger entry with
the budget snapshot, enriches the TickOutcome the heartbeat surfaces, and — critically — a
bounded long-running serve keeps an active goal/tick progressing without stalling. Hermetic:
``$FORGEKIT_HOME`` tempdir, injected config (no network), deterministic BoundedDaemon.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_provider.policy.provider_config import load_provider_config
from forgekit_runtime.runtime import tick_ledger as TL
from forgekit_runtime.runtime.daemon import BoundedDaemon, TickOutcome
from forgekit_runtime.runtime.provider_continuity import budget_snapshot, with_provider_continuity
from forgekit_runtime.runtime.provider_lane import (
    TRANSPORT_LIVE,
    TRANSPORT_NONE,
    TRANSPORT_PARTICIPANT,
    resolve_tick_lane,
)
from forgekit_runtime.runtime.surface import provider_lane_lines

_LIVE = {"primary_provider": "gemini", "linked_providers": ["gemini", "claude"]}
_PARTICIPANT = {"primary_provider": "claude", "linked_providers": ["claude"]}
_FALLBACK = {"primary_provider": "claude", "linked_providers": ["claude", "gemini"],
             "fallback_policy": {"slot_fallback_orders": {"execution": ["gemini"]}}}


def _lane(cfg_dict):
    return resolve_tick_lane(load_provider_config(cfg_dict))


# ── honest lane resolution: brain vs actual transport vs fallback ─────────────
class LaneResolutionTests(unittest.TestCase):
    def test_live_transport_named(self) -> None:
        lane = _lane(_LIVE)
        self.assertEqual(lane.transport_kind, TRANSPORT_LIVE)
        self.assertEqual(lane.actual_transport, "gemini")
        self.assertTrue(lane.live)
        self.assertIn("live", lane.label())

    def test_cli_brain_is_participant_not_live(self) -> None:
        lane = _lane(_PARTICIPANT)
        self.assertEqual(lane.transport_kind, TRANSPORT_PARTICIPANT)
        self.assertEqual(lane.brain, "claude")          # named as a participant
        self.assertEqual(lane.actual_transport, "")     # NOT faked into a live lane
        self.assertFalse(lane.live)
        self.assertIn("unsupported_in_console", lane.label())

    def test_fallback_to_live_chain(self) -> None:
        lane = _lane(_FALLBACK)
        self.assertEqual(lane.transport_kind, TRANSPORT_LIVE)
        self.assertEqual(lane.actual_transport, "gemini")
        self.assertTrue(lane.fallback_used)
        self.assertEqual(lane.fallback_chain, ("claude", "gemini"))

    def test_no_config_is_none(self) -> None:
        lane = _lane({})
        self.assertEqual(lane.transport_kind, TRANSPORT_NONE)

    def test_lane_round_trips(self) -> None:
        from forgekit_runtime.runtime.provider_lane import TickProviderLane
        lane = _lane(_FALLBACK)
        self.assertEqual(TickProviderLane.from_dict(lane.to_dict()).to_dict(), lane.to_dict())


# ── per-tick continuity: ledger + budget + outcome enrichment ────────────────
class ContinuityWrapperTests(unittest.TestCase):
    def _base(self, n):
        return TickOutcome(summary="autopilot: 1 exec", executed=1, executed_paths=("x.py",))

    def test_tick_records_lane_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = {"FORGEKIT_HOME": d}
            cfg = {**_LIVE, "daily_token_budget": 5000}
            wrapped = with_provider_continuity(self._base, config=cfg, env=env)
            out = wrapped(1)
            # outcome enriched (heartbeat will surface it)
            self.assertEqual(out.provider_lane["transport_kind"], TRANSPORT_LIVE)
            self.assertEqual(out.budget["budget"], 5000)
            self.assertIn("lane", out.summary)
            self.assertIn("budget", out.summary)
            # durable record written
            recs = TL.read_tick_records(env=env)
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0].executed, 1)
            self.assertEqual(recs[0].provider_lane.actual_transport, "gemini")
            self.assertEqual(recs[0].executed_paths, ("x.py",))

    def test_participant_lane_recorded_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = {"FORGEKIT_HOME": d}
            wrapped = with_provider_continuity(self._base, config=_PARTICIPANT, env=env)
            out = wrapped(1)
            self.assertEqual(out.provider_lane["transport_kind"], TRANSPORT_PARTICIPANT)
            self.assertFalse(out.provider_lane["live"])

    def test_budget_snapshot_unbounded(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bs = budget_snapshot({"primary_provider": "gemini"}, env={"FORGEKIT_HOME": d})
            self.assertEqual(bs.budget, 0)      # no daily_token_budget → unbounded (honest)
            self.assertFalse(bs.over)

    def test_continuity_is_additive_not_blocking(self) -> None:
        # a base tick that itself errors is the base's concern; the wrapper must not swallow
        # the base's OUTPUT — it only adds lane/budget. Here the base returns cleanly.
        with tempfile.TemporaryDirectory() as d:
            env = {"FORGEKIT_HOME": d}
            wrapped = with_provider_continuity(lambda n: TickOutcome(summary="s"),
                                               config=_LIVE, env=env, append=False)
            out = wrapped(3)
            self.assertEqual(out.summary.split(" | ")[0], "s")   # base summary preserved
            self.assertEqual(TL.read_tick_records(env=env), ())   # append=False → no write


# ── acceptance #5: long-running bounded progression does NOT stall ───────────
class LongRunningProgressionTests(unittest.TestCase):
    def test_bounded_serve_keeps_progressing(self) -> None:
        """A many-tick bounded serve advances a step EVERY tick — no stall, lane recorded each."""

        with tempfile.TemporaryDirectory() as d:
            env = {"FORGEKIT_HOME": d}
            progressed = {"steps": 0}

            def base_tick(n):
                # a safe-class step advances every tick (simulates goal exec progression)
                progressed["steps"] += 1
                return TickOutcome(summary=f"step {n}", executed=1, executed_paths=(f"s{n}.py",))

            tick_fn = with_provider_continuity(base_tick, config={**_LIVE, "daily_token_budget": 0},
                                               env=env)
            daemon = BoundedDaemon(poll_interval=0.0, max_ticks=12, sleep_fn=lambda s: None,
                                   heartbeat_path=Path(d) / "hb.json",
                                   kill_switch_path=Path(d) / "kill")
            res = daemon.serve(tick_fn)
            # ran the full bounded set, every tick executed (no stall / no early halt)
            self.assertEqual(res.ticks, 12)
            self.assertEqual(res.executed, 12)
            self.assertEqual(progressed["steps"], 12)
            # a durable per-tick lane+budget trail exists for the whole run
            recs = TL.read_tick_records(env=env)
            self.assertEqual(len(recs), 12)
            self.assertTrue(all(r.provider_lane.live for r in recs))
            self.assertEqual([r.tick for r in recs], list(range(1, 13)))

    def test_surface_reflects_recent_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            env = {"FORGEKIT_HOME": d}
            wrapped = with_provider_continuity(
                lambda n: TickOutcome(summary=f"t{n}", executed=1), config=_FALLBACK, env=env)
            for n in range(1, 4):
                wrapped(n)
            text = "\n".join(provider_lane_lines(env=env))
            self.assertIn("provider lane", text)
            self.assertIn("fallback", text)     # fallback chain surfaced
            self.assertIn("tick 3", text)


if __name__ == "__main__":
    unittest.main()
