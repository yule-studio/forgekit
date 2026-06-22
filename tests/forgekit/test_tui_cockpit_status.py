"""Operator-cockpit status line (GW5) — provider/goal/approval/budget visible at a glance.

The console issue line already showed the runtime mode posture (routing/usage/approval/loop).
The remaining operator-cockpit gap was that the two control-plane facts an operator most needs
— how many goals are PARKED awaiting their approval, and how much of today's token budget is
spent — were only reachable by POLLING (`/goal awaiting`, `/usage`). This lane proves those are
now surfaced on the persistent issue line, from the REAL stores (goal store + usage ledger),
never a fabricated number; and that the badges default OFF so existing callers are unchanged.

Three layers, each measured:
  1. pure render — runtime_mode_line badges (backward compatible by default);
  2. real wiring — ForgekitConsoleApp._cockpit_badges() reads the live goal store + ledger;
  3. live surface — a mounted pilot shows the awaiting badge on the #issue widget.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.tui import render

_TEXTUAL = importlib.util.find_spec("textual") is not None


# --- layer 1: pure render (backward compatible by default) ------------------
class RuntimeModeLineBadgeTests(unittest.TestCase):
    def _base_kwargs(self):
        return dict(label="auto", policy_mode="balanced", usage_mode="live",
                    approval="internal", loop=True)

    def test_default_has_no_cockpit_badges(self) -> None:
        # existing callers/tests must be unchanged: no awaiting / no budget badge.
        line = render.runtime_mode_line(**self._base_kwargs())
        self.assertIn("auto", line)
        self.assertNotIn("승인대기", line)
        self.assertNotIn("budget", line)

    def test_awaiting_badge_warns_and_carries_action_pointer(self) -> None:
        line = render.runtime_mode_line(**self._base_kwargs(), awaiting=2)
        self.assertIn("2 승인대기", line)
        self.assertIn("/goal awaiting", line)       # actionable, not just a number
        self.assertIn(render.theme.WARNING, line)    # warn-coloured so it can't be missed

    def test_zero_awaiting_is_silent(self) -> None:
        line = render.runtime_mode_line(**self._base_kwargs(), awaiting=0)
        self.assertNotIn("승인대기", line)

    def test_budget_badge_dim_below_threshold_warn_at_ceiling(self) -> None:
        low = render.runtime_mode_line(**self._base_kwargs(), budget_ratio=0.42)
        self.assertIn("budget 42%", low)
        self.assertIn("[dim]· budget 42%[/dim]", low)   # quiet when there's headroom
        high = render.runtime_mode_line(**self._base_kwargs(), budget_ratio=0.95)
        self.assertIn("budget 95%", high)
        self.assertIn(f"[{render.theme.WARNING}]· budget 95%", high)  # warn near the limit


# --- layer 2 + 3: real wiring + live surface --------------------------------
def _awaiting_goal(env, title="harden auth"):
    """Park a real goal in awaiting_approval in the env's goal store (no fakes)."""

    from forgekit_goal import Goal, GoalStatus, GoalStore, transitions

    store = GoalStore(env=env)
    g = transitions.apply(Goal.create(title), GoalStatus.ACTIVE)
    g = transitions.apply(g, GoalStatus.AWAITING_APPROVAL)
    store.save(g)
    return g


def _app(env):
    """Construct the live app (textual required) for the pilot surface test only."""

    from forgekit_console.commands.registry import load_agents, load_commands
    from forgekit_console.commands.router import ConsoleContext
    from forgekit_console.tui.app import ForgekitConsoleApp

    ctx = ConsoleContext(repo_root=Path("/tmp/repo"), agents=load_agents(),
                         commands=load_commands(), env=env)
    return ForgekitConsoleApp(
        repo_root=Path("/tmp/repo"), context=ctx, submit_service=None,
        config={"primary_provider": "ollama", "linked_providers": ["ollama"]})


class CockpitBadgesWiringTests(unittest.TestCase):
    """The REAL wiring — pure :func:`cockpit.cockpit_badges` reads the live stores. No
    textual dependency, so this runs in CI too (the app delegates to this helper)."""

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"FORGEKIT_HOME": self._home.name}

    def _badges(self, *, config=None, ledger_path=None):
        from forgekit_console.tui.cockpit import cockpit_badges

        return cockpit_badges(env=self.env, config=config, ledger_path=ledger_path)

    def test_no_awaiting_no_budget_is_zero_none(self) -> None:
        awaiting, budget_ratio = self._badges()
        self.assertEqual(awaiting, 0)
        self.assertIsNone(budget_ratio)   # no daily_token_budget configured → unbounded

    def test_awaiting_count_reads_real_goal_store(self) -> None:
        _awaiting_goal(self.env, "harden auth")
        _awaiting_goal(self.env, "rotate keys")
        awaiting, _ = self._badges()
        self.assertEqual(awaiting, 2)     # the real parked count, from the store

    def test_budget_ratio_reads_real_usage_ledger(self) -> None:
        from forgekit_console.usage import UsageEvent, append_event, now_ts

        ledger = Path(self._home.name) / "usage.jsonl"
        append_event(
            UsageEvent(ts=now_ts(), session_id="s1", mode="auto", provider="ollama",
                       model="m", category="live", input_tokens=400, output_tokens=530,
                       total_tokens=930, usage_basis="native", success=True, throttled=False),
            path=ledger,
        )
        _, budget_ratio = self._badges(config={"daily_token_budget": 1000}, ledger_path=ledger)
        self.assertIsNotNone(budget_ratio)
        self.assertAlmostEqual(budget_ratio, 0.93, places=2)   # 930 / 1000, real spend

    def test_store_read_failure_degrades_to_no_badge(self) -> None:
        # a goal store that raises must NEVER break the status line — honest no-badge.
        from forgekit_console.tui.cockpit import cockpit_badges

        awaiting, budget_ratio = cockpit_badges(env={"FORGEKIT_HOME": "\x00not-a-path"})
        self.assertEqual(awaiting, 0)
        self.assertIsNone(budget_ratio)


@unittest.skipUnless(_TEXTUAL, "textual not installed")
class CockpitIssueLineSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_issue_line_surfaces_awaiting_badge(self) -> None:
        from textual.widgets import Static

        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = {"FORGEKIT_HOME": home.name}
        _awaiting_goal(env, "harden auth")
        app = _app(env)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._refresh_issue()
            await pilot.pause()
            issue = str(app.query_one("#issue", Static).render())
            self.assertIn("승인대기", issue)   # the operator SEES it without polling


if __name__ == "__main__":
    unittest.main()
