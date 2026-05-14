"""Regression: slash-command ownership per bot role.

Bug surfaced as ``/engineer_intake`` being picked up by planning-bot and
showing ``애플리케이션이 응답하지 않았어요`` because planning-bot has no
orchestrator wired. Fix: ``register_discord_commands`` accepts a
:class:`BotRoleSet`, planning subprocess sets ``DISCORD_BOT_ROLE=planning``
and only registers planning commands; engineering gateway analogously
owns ``/engineer_*``.

These tests stub ``discord`` / ``discord.app_commands`` so they can run
without the real client installed, and verify the per-role command name
set.
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from typing import Any
from unittest.mock import MagicMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


def _install_fake_discord_modules() -> tuple[Any, Any]:
    """Install minimal fake ``discord`` + ``discord.app_commands`` modules.

    Returns the (discord, app_commands) module objects so the caller can
    uninstall them on teardown.
    """

    discord_mod = types.ModuleType("discord")

    class _Object:
        def __init__(self, *, id):
            self.id = id

    class _AllowedMentions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _NotFound(Exception):
        pass

    class _Interaction:  # for string-annotation lookups only
        pass

    discord_mod.Object = _Object
    discord_mod.AllowedMentions = _AllowedMentions
    discord_mod.NotFound = _NotFound
    discord_mod.Interaction = _Interaction

    ext_mod = types.ModuleType("discord.ext")
    discord_mod.ext = ext_mod

    app_commands_mod = types.ModuleType("discord.app_commands")

    def _describe(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap

    class _Range:
        def __class_getitem__(cls, _key):
            return int

    app_commands_mod.describe = _describe
    app_commands_mod.Range = _Range
    discord_mod.app_commands = app_commands_mod

    sys.modules["discord"] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.app_commands"] = app_commands_mod
    return discord_mod, app_commands_mod


class _FakeTree:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, *, name, description, guild=None):
        del description, guild  # only the name matters for these tests

        def _decorator(fn):
            self.commands.append(name)
            return fn

        return _decorator


class _FakeBot:
    def __init__(self) -> None:
        self.tree = _FakeTree()


class CommandRoleSplitTests(unittest.TestCase):
    """``register_discord_commands`` registers different command sets per role."""

    def setUp(self) -> None:
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("discord", "discord.ext", "discord.app_commands")
        }
        _install_fake_discord_modules()
        # Re-import the commands module against the freshly-stubbed discord
        # so the ``import discord`` inside register_discord_commands resolves
        # to our fake.
        if "yule_orchestrator.discord.commands" in sys.modules:
            del sys.modules["yule_orchestrator.discord.commands"]
        from yule_orchestrator.discord import commands as commands_module

        self.commands_module = commands_module

    def tearDown(self) -> None:
        for name, previous in self._previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        sys.modules.pop("yule_orchestrator.discord.commands", None)

    def test_planning_role_excludes_engineer_intake(self) -> None:
        bot = _FakeBot()

        self.commands_module.register_discord_commands(
            bot,
            guild_id=123,
            notify_user_id=None,
            role_set=self.commands_module.BotRoleSet.PLANNING_ONLY,
        )

        self.assertIn("ping", bot.tree.commands)
        self.assertIn("plan_today", bot.tree.commands)
        self.assertIn("checkpoints_now", bot.tree.commands)
        self.assertNotIn("engineer_intake", bot.tree.commands)
        for name in bot.tree.commands:
            self.assertFalse(
                name.startswith("engineer_"),
                msg=f"planning role should not own {name}",
            )

    def test_engineering_role_excludes_plan_today(self) -> None:
        bot = _FakeBot()

        self.commands_module.register_discord_commands(
            bot,
            guild_id=123,
            notify_user_id=None,
            role_set=self.commands_module.BotRoleSet.ENGINEERING_ONLY,
        )

        # Every engineer_* command must be registered…
        expected_engineer_commands = {
            "engineer_intake",
            "engineer_show",
            "engineer_review",
            "engineer_review_reply",
            "engineer_approve",
            "engineer_reject",
            "engineer_progress",
            "engineer_complete",
        }
        self.assertTrue(
            expected_engineer_commands.issubset(set(bot.tree.commands)),
            msg=(
                "engineering role missing commands: "
                f"{expected_engineer_commands - set(bot.tree.commands)}"
            ),
        )
        # …and no planning commands.
        self.assertNotIn("ping", bot.tree.commands)
        self.assertNotIn("plan_today", bot.tree.commands)
        self.assertNotIn("checkpoints_now", bot.tree.commands)

    def test_all_role_back_compat_registers_both_sets(self) -> None:
        bot = _FakeBot()

        self.commands_module.register_discord_commands(
            bot,
            guild_id=123,
            notify_user_id=None,
            role_set=self.commands_module.BotRoleSet.ALL,
        )

        self.assertIn("ping", bot.tree.commands)
        self.assertIn("engineer_intake", bot.tree.commands)
        self.assertIn("checkpoints_now", bot.tree.commands)

    def test_default_role_set_is_all_for_back_compat(self) -> None:
        bot = _FakeBot()

        # Caller does not pass role_set — should behave like ALL.
        self.commands_module.register_discord_commands(
            bot,
            guild_id=123,
            notify_user_id=None,
        )

        self.assertIn("ping", bot.tree.commands)
        self.assertIn("engineer_intake", bot.tree.commands)

    def test_convenience_wrappers_match_role_sets(self) -> None:
        planning_bot = _FakeBot()
        engineering_bot = _FakeBot()

        self.commands_module.register_planning_commands(planning_bot, guild_id=123)
        self.commands_module.register_engineering_commands(engineering_bot, guild_id=123)

        self.assertIn("ping", planning_bot.tree.commands)
        self.assertNotIn("engineer_intake", planning_bot.tree.commands)

        self.assertIn("engineer_intake", engineering_bot.tree.commands)
        self.assertNotIn("ping", engineering_bot.tree.commands)


class BotRoleSetEnvResolutionTests(unittest.TestCase):
    """``resolve_bot_role_set_from_env`` interprets ``DISCORD_BOT_ROLE``."""

    def setUp(self) -> None:
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("discord", "discord.ext", "discord.app_commands")
        }
        _install_fake_discord_modules()
        if "yule_orchestrator.discord.commands" in sys.modules:
            del sys.modules["yule_orchestrator.discord.commands"]
        from yule_orchestrator.discord import commands as commands_module

        self.commands_module = commands_module

    def tearDown(self) -> None:
        for name, previous in self._previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        sys.modules.pop("yule_orchestrator.discord.commands", None)

    def test_planning_env_maps_to_planning_only(self) -> None:
        result = self.commands_module.resolve_bot_role_set_from_env(
            {"DISCORD_BOT_ROLE": "planning"}
        )
        self.assertEqual(result, self.commands_module.BotRoleSet.PLANNING_ONLY)

    def test_engineering_gateway_env_maps_to_engineering_only(self) -> None:
        result = self.commands_module.resolve_bot_role_set_from_env(
            {"DISCORD_BOT_ROLE": "engineering-gateway"}
        )
        self.assertEqual(result, self.commands_module.BotRoleSet.ENGINEERING_ONLY)

    def test_unset_env_falls_back_to_all(self) -> None:
        result = self.commands_module.resolve_bot_role_set_from_env({})
        self.assertEqual(result, self.commands_module.BotRoleSet.ALL)

    def test_unknown_env_falls_back_to_all(self) -> None:
        result = self.commands_module.resolve_bot_role_set_from_env(
            {"DISCORD_BOT_ROLE": "something-else"}
        )
        self.assertEqual(result, self.commands_module.BotRoleSet.ALL)

    def test_whitespace_and_case_are_normalized(self) -> None:
        result = self.commands_module.resolve_bot_role_set_from_env(
            {"DISCORD_BOT_ROLE": "  Engineering-Gateway  "}
        )
        self.assertEqual(result, self.commands_module.BotRoleSet.ENGINEERING_ONLY)


class UnexpectedErrorFollowupTests(unittest.TestCase):
    """Unexpected exceptions in /engineer_intake surface to Discord followup."""

    def setUp(self) -> None:
        self._previous_modules = {
            name: sys.modules.get(name)
            for name in ("discord", "discord.ext", "discord.app_commands")
        }
        _install_fake_discord_modules()
        if "yule_orchestrator.discord.commands" in sys.modules:
            del sys.modules["yule_orchestrator.discord.commands"]
        from yule_orchestrator.discord import commands as commands_module

        self.commands_module = commands_module

    def tearDown(self) -> None:
        for name, previous in self._previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        sys.modules.pop("yule_orchestrator.discord.commands", None)

    def test_surface_unexpected_error_calls_followup_send(self) -> None:
        followup = MagicMock()

        async def _send(text):
            followup.last_text = text

        followup.send = _send

        interaction = MagicMock()
        interaction.followup = followup

        async def run() -> None:
            await self.commands_module._surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_intake",
                exc=RuntimeError("boom"),
                discord_module=sys.modules["discord"],
            )

        asyncio.run(run())

        self.assertIn("engineer_intake", followup.last_text)
        self.assertIn("RuntimeError", followup.last_text)
        self.assertIn("boom", followup.last_text)

    def test_engineer_intake_handler_routes_unexpected_exception_to_followup(self) -> None:
        # Capture the registered handler closure so we can call it directly
        # with a fake interaction that triggers an unexpected exception.
        captured_handlers: dict[str, Any] = {}

        class CapturingTree:
            def command(self, *, name, description, guild=None):
                del description, guild

                def _decorator(fn):
                    captured_handlers[name] = fn
                    return fn

                return _decorator

        class CapturingBot:
            def __init__(self) -> None:
                self.tree = CapturingTree()

        bot = CapturingBot()
        self.commands_module.register_discord_commands(
            bot,
            guild_id=123,
            role_set=self.commands_module.BotRoleSet.ENGINEERING_ONLY,
        )

        self.assertIn("engineer_intake", captured_handlers)
        handler = captured_handlers["engineer_intake"]

        followup_messages: list[str] = []
        response_messages: list[str] = []

        class FakeResponse:
            async def defer(self, *, thinking=True):
                # Trigger an unexpected exception path: simulate a runtime
                # error in defer itself (NOT a discord.NotFound). _safe_defer
                # will re-raise -> outer broad except in the handler runs.
                raise RuntimeError("unexpected defer crash")

            async def send_message(self, text):
                response_messages.append(text)

        class FakeFollowup:
            async def send(self, text, allowed_mentions=None):
                del allowed_mentions
                followup_messages.append(text)

        class FakeUser:
            id = 42

        class FakeInteraction:
            command = type("C", (), {"name": "engineer_intake"})()
            user = FakeUser()
            channel_id = 999
            response = FakeResponse()
            followup = FakeFollowup()

        async def run() -> None:
            await handler(
                FakeInteraction(),
                prompt="prompt",
                task_type=None,
                write_requested=False,
            )

        asyncio.run(run())

        # We expect ONE followup message that names the command and the
        # exception type — the planning-bot-style timeout no longer occurs.
        self.assertEqual(len(followup_messages), 1)
        body = followup_messages[0]
        self.assertIn("engineer_intake", body)
        self.assertIn("RuntimeError", body)
        self.assertIn("unexpected defer crash", body)


if __name__ == "__main__":
    unittest.main()
