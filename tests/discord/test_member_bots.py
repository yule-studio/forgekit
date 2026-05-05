from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.formatter import format_references_block
from yule_orchestrator.discord.member_bot import (
    _PermissionTarget,
    _member_bot_startup_permission_lines,
)
from yule_orchestrator.discord.member_bots import (
    GATEWAY_ROLE_KEY,
    MemberBotProfile,
    env_key_for,
    load_member_bot_config,
    render_startup_summary,
    select_profile_for_role,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class EnvKeyTestCase(unittest.TestCase):
    def test_gateway_key(self) -> None:
        self.assertEqual(
            env_key_for("engineering-agent", GATEWAY_ROLE_KEY),
            "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN",
        )

    def test_member_key(self) -> None:
        self.assertEqual(
            env_key_for("engineering-agent", "backend-engineer"),
            "ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN",
        )

    def test_other_department_prefix(self) -> None:
        self.assertEqual(
            env_key_for("design-agent", "product-designer"),
            "DESIGN_AGENT_BOT_PRODUCT_DESIGNER_TOKEN",
        )


class LoadMemberBotConfigTestCase(unittest.TestCase):
    def test_engineering_agent_lists_gateway_and_members(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        self.assertEqual(
            config.role_ids(),
            (
                GATEWAY_ROLE_KEY,
                "tech-lead",
                "ai-engineer",
                "product-designer",
                "backend-engineer",
                "frontend-engineer",
                "qa-engineer",
                "devops-engineer",
            ),
        )
        for profile in config.profiles:
            self.assertFalse(profile.active)

    def test_token_in_env_marks_profile_active(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENGINEERING_AGENT_BOT_")}
        env["ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN"] = "abc"
        with patch.dict(os.environ, env, clear=True):
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")
            profile = config.get("backend-engineer")
            self.assertTrue(profile.active)
            self.assertEqual(profile.token, "abc")

    def test_unknown_agent_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            load_member_bot_config(REPO_ROOT, "no-such-agent")

    def test_ai_engineer_role_is_registered_with_expected_env_key(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        ai_engineer = config.get("ai-engineer")
        self.assertEqual(
            ai_engineer.env_key,
            "ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN",
        )
        self.assertFalse(ai_engineer.active)

    def test_ai_engineer_token_in_env_marks_profile_active(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENGINEERING_AGENT_BOT_")}
        env["ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN"] = "ai-token"
        with patch.dict(os.environ, env, clear=True):
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")
            profile = config.get("ai-engineer")
            self.assertTrue(profile.active)
            self.assertEqual(profile.token, "ai-token")

    def test_devops_engineer_role_is_registered_with_expected_env_key(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        devops = config.get("devops-engineer")
        self.assertEqual(
            devops.env_key,
            "ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN",
        )
        self.assertFalse(devops.active)

    def test_devops_engineer_token_in_env_marks_profile_active(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENGINEERING_AGENT_BOT_")}
        env["ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN"] = "devops-token"
        with patch.dict(os.environ, env, clear=True):
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")
            profile = config.get("devops-engineer")
            self.assertTrue(profile.active)
            self.assertEqual(profile.token, "devops-token")

    def test_ai_and_devops_both_active_when_both_tokens_set(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENGINEERING_AGENT_BOT_")}
        env["ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN"] = "ai-tok"
        env["ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN"] = "devops-tok"
        with patch.dict(os.environ, env, clear=True):
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")
            active_roles = {p.role for p in config.active_profiles()}
        self.assertIn("ai-engineer", active_roles)
        self.assertIn("devops-engineer", active_roles)


class SelectProfileTestCase(unittest.TestCase):
    def test_unknown_role_lists_available(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        with self.assertRaises(ValueError) as ctx:
            select_profile_for_role(config, "phantom", require_token=False)

        message = str(ctx.exception)
        self.assertIn("phantom", message)
        self.assertIn("backend-engineer", message)

    def test_missing_token_blocks_real_run(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        with self.assertRaises(ValueError) as ctx:
            select_profile_for_role(config, "tech-lead")

        self.assertIn("ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN", str(ctx.exception))

    def test_dry_run_allows_inactive_profile(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("ENGINEERING_AGENT_BOT_"):
                    del os.environ[key]
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        profile = select_profile_for_role(config, "tech-lead", require_token=False)
        self.assertFalse(profile.active)


class StartupSummaryTestCase(unittest.TestCase):
    def test_summary_includes_status_and_env_key(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("ENGINEERING_AGENT_BOT_")}
        env["ENGINEERING_AGENT_BOT_GATEWAY_TOKEN"] = "tok"
        with patch.dict(os.environ, env, clear=True):
            config = load_member_bot_config(REPO_ROOT, "engineering-agent")

        lines = render_startup_summary(config)
        joined = "\n".join(lines)
        self.assertIn("engineering-agent (gateway): active", joined)
        self.assertIn("engineering-agent/qa-engineer: skipped", joined)
        self.assertIn("ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN", joined)


class MemberBotPermissionStartupTestCase(unittest.TestCase):
    def test_reports_ok_when_required_thread_permissions_exist(self) -> None:
        profile = self._profile("tech-lead")
        channel = _FakeChannel(
            channel_id=42,
            name="운영-리서치",
            permissions=_FakePermissions(),
        )
        guild = _FakeGuild(guild_id=1, channels=(channel,))
        bot = _FakeBot(guild)

        lines = _member_bot_startup_permission_lines(
            profile=profile,
            bot=bot,
            guild_id=1,
            targets=(
                _PermissionTarget(
                    label="운영-리서치 forum",
                    channel_id=42,
                    channel_name=None,
                    env_hint="DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*",
                ),
            ),
        )

        joined = "\n".join(lines)
        self.assertIn("Message Content Intent", joined)
        self.assertIn("permissions OK", joined)

    def test_reports_missing_thread_send_permission(self) -> None:
        profile = self._profile("qa-engineer")
        channel = _FakeChannel(
            channel_id=42,
            name="운영-리서치",
            permissions=_FakePermissions(send_messages_in_threads=False),
        )
        guild = _FakeGuild(guild_id=1, channels=(channel,))
        bot = _FakeBot(guild)

        lines = _member_bot_startup_permission_lines(
            profile=profile,
            bot=bot,
            guild_id=1,
            targets=(
                _PermissionTarget(
                    label="운영-리서치 forum",
                    channel_id=42,
                    channel_name=None,
                    env_hint="DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_*",
                ),
            ),
        )

        joined = "\n".join(lines)
        self.assertIn("missing 운영-리서치 forum permissions", joined)
        self.assertIn("Send Messages in Threads", joined)

    def test_reports_unresolved_channel(self) -> None:
        profile = self._profile("backend-engineer")
        guild = _FakeGuild(guild_id=1, channels=())
        bot = _FakeBot(guild)

        lines = _member_bot_startup_permission_lines(
            profile=profile,
            bot=bot,
            guild_id=1,
            targets=(
                _PermissionTarget(
                    label="업무-접수 thread parent",
                    channel_id=None,
                    channel_name="업무-접수",
                    env_hint="DISCORD_ENGINEERING_INTAKE_CHANNEL_*",
                ),
            ),
        )

        self.assertIn("cannot resolve 업무-접수 thread parent", "\n".join(lines))

    @staticmethod
    def _profile(role: str) -> MemberBotProfile:
        return MemberBotProfile(
            agent_id="engineering-agent",
            role=role,
            env_key=f"ENGINEERING_AGENT_BOT_{role.upper().replace('-', '_')}_TOKEN",
            token="token",
            display_label=f"engineering-agent/{role}",
        )


class _FakePermissions:
    def __init__(
        self,
        *,
        view_channel: bool = True,
        read_message_history: bool = True,
        send_messages: bool = True,
        send_messages_in_threads: bool = True,
    ) -> None:
        self.view_channel = view_channel
        self.read_message_history = read_message_history
        self.send_messages = send_messages
        self.send_messages_in_threads = send_messages_in_threads


class _FakeChannel:
    def __init__(self, *, channel_id: int, name: str, permissions: _FakePermissions) -> None:
        self.id = channel_id
        self.name = name
        self._permissions = permissions

    def permissions_for(self, _member):
        return self._permissions


class _FakeGuild:
    def __init__(self, *, guild_id: int, channels: tuple[_FakeChannel, ...]) -> None:
        self.id = guild_id
        self.channels = channels
        self.me = SimpleNamespace(id=123)

    def get_channel(self, channel_id: int):
        for channel in self.channels:
            if channel.id == channel_id:
                return channel
        return None


class _FakeBot:
    def __init__(self, guild: _FakeGuild) -> None:
        self.guilds = (guild,)
        self._guild = guild

    def get_guild(self, guild_id: int):
        if self._guild.id == guild_id:
            return self._guild
        return None

    def get_channel(self, channel_id: int):
        return self._guild.get_channel(channel_id)


class ReferencesBlockTestCase(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        self.assertEqual(format_references_block([]), "")

    def test_renders_title_source_url_takeaway(self) -> None:
        block = format_references_block(
            [
                {
                    "title": "Stripe Pricing",
                    "source": "Mobbin",
                    "url": "https://example.com/stripe",
                    "takeaway": "step copy 시각 강조 차용",
                },
                {"title": "Naked Wines"},
            ]
        )
        self.assertIn("**참고 레퍼런스**", block)
        self.assertIn("Stripe Pricing", block)
        self.assertIn("Mobbin", block)
        self.assertIn("https://example.com/stripe", block)
        self.assertIn("step copy", block)
        self.assertIn("Naked Wines", block)

    def test_limit_truncates_to_top_n(self) -> None:
        items = [{"title": f"item-{i}"} for i in range(10)]
        block = format_references_block(items, limit=3)
        self.assertEqual(block.count("item-"), 3)


if __name__ == "__main__":
    unittest.main()
