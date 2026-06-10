"""gateway env override helper — A-M6.1b-2 unit tests.

Pin that the override mapping always blanks the planning-bot
channel keys + flips DISCORD_BOT_TOKEN to the engineering gateway
token. The legacy ``yule discord up`` and the new
``yule run-service eng-discord-gateway`` both read this — drift in
either direction would let the gateway respond on the planning
bot's channels.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.runtime.gateway_env import (
    GATEWAY_TOKEN_ENV,
    build_gateway_env_overrides,
    resolve_gateway_token,
)


class BuildGatewayEnvOverridesTests(unittest.TestCase):
    def test_token_override_replaces_discord_bot_token(self) -> None:
        env = build_gateway_env_overrides(
            gateway_token="gw-tok",
            base_env={
                "DISCORD_BOT_TOKEN": "planning-tok",
                "OTHER_KEY": "keep",
            },
        )
        self.assertEqual(env["DISCORD_BOT_TOKEN"], "gw-tok")
        self.assertEqual(env["OTHER_KEY"], "keep")

    def test_planning_channel_keys_are_blanked(self) -> None:
        env = build_gateway_env_overrides(
            gateway_token="gw-tok",
            base_env={
                "DISCORD_DAILY_CHANNEL_ID": "111",
                "DISCORD_DAILY_CHANNEL_NAME": "일정-관리",
                "DISCORD_CHECKPOINT_CHANNEL_ID": "222",
                "DISCORD_CONVERSATION_CHANNEL_ID": "333",
                "DISCORD_NOTIFY_USER_ID": "user-1",
                "DISCORD_DEBUG_CHANNEL_ID": "444",
            },
        )
        # Every planning-bot channel key blanked so the gateway
        # never sees them — must NOT respond on those channels.
        for key in (
            "DISCORD_DAILY_CHANNEL_ID",
            "DISCORD_DAILY_CHANNEL_NAME",
            "DISCORD_CHECKPOINT_CHANNEL_ID",
            "DISCORD_CONVERSATION_CHANNEL_ID",
            "DISCORD_NOTIFY_USER_ID",
            "DISCORD_DEBUG_CHANNEL_ID",
        ):
            with self.subTest(key=key):
                self.assertEqual(env[key], "")

    def test_conversation_reply_mode_set_to_disabled(self) -> None:
        env = build_gateway_env_overrides(
            gateway_token="gw-tok", base_env={}
        )
        # Even when conversation reply mode wasn't set, the override
        # forces it to "disabled" so the gateway can't accidentally
        # respond as the planning bot would.
        self.assertEqual(env["DISCORD_CONVERSATION_REPLY_MODE"], "disabled")

    def test_empty_token_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_gateway_env_overrides(gateway_token="", base_env={})
        with self.assertRaises(ValueError):
            build_gateway_env_overrides(gateway_token="   ", base_env={})


class ResolveGatewayTokenTests(unittest.TestCase):
    def test_returns_token_when_set(self) -> None:
        self.assertEqual(
            resolve_gateway_token({GATEWAY_TOKEN_ENV: "gw-tok"}),
            "gw-tok",
        )

    def test_returns_none_when_unset_or_blank(self) -> None:
        self.assertIsNone(resolve_gateway_token({}))
        self.assertIsNone(
            resolve_gateway_token({GATEWAY_TOKEN_ENV: "  "})
        )


if __name__ == "__main__":
    unittest.main()
