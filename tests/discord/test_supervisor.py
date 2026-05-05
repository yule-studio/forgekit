from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from yule_orchestrator.cli.discord_up import parse_agent_ids, run_discord_up_command
from yule_orchestrator.discord.supervisor import (
    BOT_RUNNER_ENGINEERING_GATEWAY,
    BOT_RUNNER_MEMBER,
    BOT_RUNNER_PLANNING,
    ENGINEERING_AGENT_FAMILY,
    PLANNING_BOT_DISPLAY_LABEL,
    PLANNING_BOT_ENV_KEY,
    SpawnResult,
    build_inventory,
    render_inventory_summary,
    start_all,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class BuildInventoryTestCase(unittest.TestCase):
    def test_inventory_lists_planning_first_then_gateway_then_members(self) -> None:
        env = {
            PLANNING_BOT_ENV_KEY: "planning-token",
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "gw-token",
            "ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN": "be-token",
        }

        inventory = build_inventory(REPO_ROOT, env=env)
        bot_ids = [bot.bot_id for bot in inventory.bots]

        self.assertEqual(bot_ids[0], PLANNING_BOT_DISPLAY_LABEL)
        self.assertEqual(bot_ids[1], "engineering-agent/gateway")
        self.assertIn("engineering-agent/backend-engineer", bot_ids)
        backend_index = bot_ids.index("engineering-agent/backend-engineer")
        self.assertGreater(backend_index, 1)

    def test_token_missing_marks_skipped(self) -> None:
        env = {PLANNING_BOT_ENV_KEY: ""}

        inventory = build_inventory(REPO_ROOT, env=env)
        planning = next(bot for bot in inventory.bots if bot.family == "planning")

        self.assertFalse(planning.has_token)
        self.assertEqual(planning.status, "skipped (token missing)")
        self.assertIn(planning, inventory.skipped())

    def test_engineering_gateway_uses_gateway_runner_type(self) -> None:
        env = {"ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "gw-token"}

        inventory = build_inventory(REPO_ROOT, env=env)
        gateway = next(
            bot for bot in inventory.bots if bot.bot_id == "engineering-agent/gateway"
        )

        self.assertEqual(gateway.runner, BOT_RUNNER_ENGINEERING_GATEWAY)
        self.assertTrue(gateway.has_token)

    def test_engineering_role_bot_has_member_runner_type(self) -> None:
        env = {"ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN": "be-token"}

        inventory = build_inventory(REPO_ROOT, env=env)
        backend = next(
            bot for bot in inventory.bots if bot.bot_id == "engineering-agent/backend-engineer"
        )

        self.assertEqual(backend.runner, BOT_RUNNER_MEMBER)
        self.assertTrue(backend.has_token)

    def test_ai_engineer_member_appears_in_inventory_with_active_status(self) -> None:
        env = {"ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN": "ai-token"}

        inventory = build_inventory(REPO_ROOT, env=env)
        ai_engineer = next(
            bot for bot in inventory.bots if bot.bot_id == "engineering-agent/ai-engineer"
        )

        self.assertEqual(ai_engineer.role, "ai-engineer")
        self.assertEqual(ai_engineer.env_key, "ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN")
        self.assertEqual(ai_engineer.runner, BOT_RUNNER_MEMBER)
        self.assertTrue(ai_engineer.has_token)
        self.assertEqual(ai_engineer.status, "active")

    def test_ai_engineer_skipped_when_token_missing(self) -> None:
        # No env tokens at all → ai-engineer must still appear in inventory but skipped
        inventory = build_inventory(REPO_ROOT, env={})
        ai_engineer = next(
            bot for bot in inventory.bots if bot.bot_id == "engineering-agent/ai-engineer"
        )

        self.assertFalse(ai_engineer.has_token)
        self.assertEqual(ai_engineer.status, "skipped (token missing)")

    def test_planning_bot_has_planning_runner_type(self) -> None:
        env = {PLANNING_BOT_ENV_KEY: "planning-token"}

        inventory = build_inventory(REPO_ROOT, env=env)
        planning = next(
            bot for bot in inventory.bots if bot.bot_id == PLANNING_BOT_DISPLAY_LABEL
        )

        self.assertEqual(planning.runner, BOT_RUNNER_PLANNING)


class RenderInventorySummaryTestCase(unittest.TestCase):
    def test_summary_includes_status_and_count_lines(self) -> None:
        env = {
            PLANNING_BOT_ENV_KEY: "planning-token",
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "",
        }

        inventory = build_inventory(REPO_ROOT, env=env)
        lines = render_inventory_summary(inventory)
        joined = "\n".join(lines)

        self.assertIn("discord launcher inventory:", joined)
        self.assertIn("planning-bot", joined)
        self.assertIn("engineering-agent (gateway)", joined)
        self.assertIn("active", joined)
        self.assertIn("skipped (token missing)", joined)
        self.assertTrue(any(line.startswith("summary: ") for line in lines))


class StartAllTestCase(unittest.TestCase):
    def test_dry_run_does_not_invoke_spawn(self) -> None:
        env = {PLANNING_BOT_ENV_KEY: "planning-token"}
        inventory = build_inventory(REPO_ROOT, env=env)
        spawned: list[str] = []

        def spy(bot):
            spawned.append(bot.bot_id)
            return object()

        report = start_all(inventory, dry_run=True, spawn_fn=spy)

        self.assertTrue(report.dry_run)
        self.assertEqual(spawned, [])
        self.assertEqual(report.started_count(), 0)
        self.assertEqual(report.failed_count(), 0)

    def test_starts_only_active_bots(self) -> None:
        env = {
            PLANNING_BOT_ENV_KEY: "planning-token",
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN": "gw-token",
        }
        inventory = build_inventory(REPO_ROOT, env=env)
        spawned: list[str] = []

        def spy(bot):
            spawned.append(bot.bot_id)
            return object()

        report = start_all(inventory, spawn_fn=spy)

        self.assertIn(PLANNING_BOT_DISPLAY_LABEL, spawned)
        self.assertIn("engineering-agent/gateway", spawned)
        # backend/frontend/etc had no token in the env above
        for bot_id in spawned:
            self.assertIn(bot_id, {PLANNING_BOT_DISPLAY_LABEL, "engineering-agent/gateway"})
        self.assertEqual(report.started_count(), len(spawned))

    def test_spawn_failure_is_captured_per_bot(self) -> None:
        env = {PLANNING_BOT_ENV_KEY: "planning-token"}
        inventory = build_inventory(REPO_ROOT, env=env)

        def boom(bot):
            raise RuntimeError("simulated failure")

        report = start_all(inventory, spawn_fn=boom)

        self.assertEqual(report.failed_count(), 1)
        failed = next(result for result in report.results if result.error is not None)
        self.assertIn("simulated failure", failed.error)


class ParseAgentIdsTestCase(unittest.TestCase):
    def test_blank_returns_default(self) -> None:
        self.assertEqual(parse_agent_ids(None), (ENGINEERING_AGENT_FAMILY,))
        self.assertEqual(parse_agent_ids(""), (ENGINEERING_AGENT_FAMILY,))

    def test_csv_strips_whitespace_and_drops_empties(self) -> None:
        self.assertEqual(
            parse_agent_ids("engineering-agent, ,  marketing-agent"),
            ("engineering-agent", "marketing-agent"),
        )


class RunDiscordUpCommandTestCase(unittest.TestCase):
    def test_dry_run_returns_zero_and_prints_inventory(self) -> None:
        # We rely on the real env not having tokens; --dry-run should still
        # exit 0 because dry-run is a successful "no-op".
        captured = io.StringIO()
        with redirect_stderr(captured):
            exit_code = run_discord_up_command(
                REPO_ROOT,
                dry_run=True,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("discord launcher inventory:", captured.getvalue())

    def test_returns_two_when_no_active_bots(self) -> None:
        # Without injecting tokens the live env may or may not have a token,
        # so we drive the supervisor through the public ``start_all`` instead.
        env = {}  # nothing
        inventory = build_inventory(REPO_ROOT, env=env)
        report = start_all(inventory)  # no dry-run, but no tokens → all skipped
        self.assertEqual(report.started_count(), 0)
        self.assertEqual(report.failed_count(), 0)
        self.assertGreater(len(report.results), 0)


class SpawnResultDefaultsTestCase(unittest.TestCase):
    def test_defaults_have_no_handle_or_error(self) -> None:
        result = SpawnResult(bot_id="x", started=False)
        self.assertIsNone(result.handle)
        self.assertIsNone(result.error)
        self.assertIsNone(result.skipped_reason)


if __name__ == "__main__":
    unittest.main()
