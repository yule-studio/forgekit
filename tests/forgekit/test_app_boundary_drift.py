"""GW6 — app→app import-boundary DRIFT guard.

The hard rail "apps/* must not import apps/*" is the goal (each app talks to
packages/* or a contracts seam, never to a sibling app). Reality today: there are
51 pre-existing app→app edges — discord-gateway composes engineering + planning,
and engineering wires a few discord/planning CLIs. That is **documented
monolith-decomposition debt** (docs/forgekit-architecture-ownership.md §1.2/§6,
docs/monorepo-structure.md §4), not something this test can pretend away.

So this guard is honest about the present while locking the boundary against the
future: it **freezes the current edge set as a baseline** and fails on any *new*
app→app edge. Paying debt down (removing an edge) is always allowed. Adding a new
cross-app import is not — use packages/* or a forgekit-contracts/agent-contracts
seam instead.

Complements tests/forgekit/test_package_topology_guard.py, which guards the
*packages→apps* direction. This one guards *app→app*. Pure / CI-safe (no imports
of the apps themselves — it only reads source text).
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
APPS = REPO / "apps"

# python package name of each runnable app (an app importing ANY of these other
# than its own = an app→app edge).
APP_PKGS = frozenset({
    "yule_engineering", "yule_planning", "yule_discord",
    "forgekit_console", "yule_memory_worker", "yule_loadtest",
})

_IMPORT = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z_][\w]*)")

# Frozen baseline of CURRENT app→app edges as "<own> -> <imported> :: <relpath>".
# This is the documented debt surface. New entries are forbidden (test fails);
# removing entries (debt paydown) is fine. Regenerate intentionally — never to
# silence a new edge.
BASELINE = frozenset({
    "forgekit_console -> yule_engineering :: apps/forgekit-console/src/forgekit_console/data/status_loader.py",
    "forgekit_console -> yule_engineering :: apps/forgekit-console/src/forgekit_console/handoff/gateway.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/approval/reply_router.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/bot/_legacy.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/bot/scheduling.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/commands/__init__.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/commands/engineering_commands.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering/discussion_turn.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering/help_surface.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering/product_intake_seam.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/__init__.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/coding_gate.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/council_flow.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/main.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/models.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/obsidian_gate.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/reporting.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/research_loop.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/runtime_preflight.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/session_persistence.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_channel_router/utils.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/__init__.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/intent_detection.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/models.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/research_bootstrap.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/response_formatters.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/status_responses.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_conversation/task_shaping.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_team_runtime/__init__.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/engineering_team_runtime/_legacy.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/forum/message_adapter.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/integrations/github_workos_adapter.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/integrations/pr_merge_adapter.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/member/bot.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/research_forum/__init__.py",
    "yule_discord -> yule_engineering :: apps/discord-gateway/src/yule_discord/runtime/supervisor.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/bot/_legacy.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/bot/scheduling.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/bot/startup.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/conversation/__init__.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/runtime/checkpoint_state.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/runtime/planning.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/runtime/snapshot_refresh.py",
    "yule_discord -> yule_planning :: apps/discord-gateway/src/yule_discord/ui/formatter.py",
    "yule_engineering -> yule_discord :: apps/engineering-agent/src/yule_engineering/cli/discord.py",
    "yule_engineering -> yule_discord :: apps/engineering-agent/src/yule_engineering/cli/discord_member.py",
    "yule_engineering -> yule_discord :: apps/engineering-agent/src/yule_engineering/cli/discord_up.py",
    "yule_engineering -> yule_discord :: apps/engineering-agent/src/yule_engineering/runtime/coding_executor_builders.py",
    "yule_engineering -> yule_discord :: apps/engineering-agent/src/yule_engineering/runtime/discord_runner.py",
    "yule_engineering -> yule_planning :: apps/engineering-agent/src/yule_engineering/cli/daily.py",
    "yule_engineering -> yule_planning :: apps/engineering-agent/src/yule_engineering/cli/planning.py",
})


def _app_own_pkg():
    """Map each app dir to the app python pkg it owns."""

    out = {}
    for app in APPS.iterdir():
        src = app / "src"
        if not src.is_dir():
            continue
        for pkg in src.iterdir():
            if pkg.is_dir() and pkg.name in APP_PKGS:
                out[app.name] = (pkg.name, src)
    return out


def current_edges():
    """Recompute the live app→app edge set as <own> -> <imported> :: <relpath>."""

    edges = set()
    for _app, (own, src) in _app_own_pkg().items():
        for py in src.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            for line in py.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = _IMPORT.match(line)
                if not m:
                    continue
                mod = m.group(1)
                if mod in APP_PKGS and mod != own:
                    rel = py.relative_to(REPO).as_posix()
                    edges.add(f"{own} -> {mod} :: {rel}")
    return edges


class AppToAppBoundaryDriftTests(unittest.TestCase):
    def test_no_new_app_to_app_edges(self) -> None:
        new = sorted(current_edges() - BASELINE)
        self.assertEqual(
            new, [],
            "NEW app→app import edge(s) introduced (forbidden hard rail). Route via "
            "packages/* or a contracts seam (forgekit-contracts / agent-contracts), "
            f"or inject a runner. New edges:\n  " + "\n  ".join(new),
        )

    def test_baseline_is_not_stale_beyond_tolerance(self) -> None:
        # Paying debt down is good; we don't fail on it. But if the baseline has
        # drifted a lot from reality, surface it so the snapshot gets refreshed
        # (kept generous so normal paydown never trips CI).
        stale = sorted(BASELINE - current_edges())
        self.assertLessEqual(
            len(stale), 10,
            "app→app baseline has many stale entries (debt paid down — good!). "
            "Refresh BASELINE in this test to match reality:\n  " + "\n  ".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
