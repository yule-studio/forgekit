"""Engineering gateway env override helper — A-M6.1b-2.

Both ``yule discord up`` (legacy dev launcher) and
``yule run-service eng-discord-gateway`` (new always-on entry)
need to spawn the gateway with the same env carved out so the
planning bot's channels stay invisible to it. Without that
override two bots would respond to the same ``#일정-관리`` /
``#업무-접수`` messages.

This helper lifts the override list out of
:mod:`discord.supervisor._run_engineering_gateway_in_subprocess`
so both entrypoints share one source of truth and a future audit
of "what env keys does the gateway see" only has one row to read.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional


GATEWAY_TOKEN_ENV: str = "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN"


# Keys the gateway sets to empty strings so the planning-bot
# pathways inside ``run_discord_bot`` stay dormant. The override
# pattern matches the legacy ``yule discord up`` spawn — operators
# who already have planning-bot env populated continue to see that
# bot react in its own channel, while the gateway only watches its
# engineering channels.
_PLANNING_BOT_KEYS_TO_BLANK: tuple[str, ...] = (
    "DISCORD_APPLICATION_ID",
    "DISCORD_DAILY_CHANNEL_ID",
    "DISCORD_DAILY_CHANNEL_NAME",
    "DISCORD_CHECKPOINT_CHANNEL_ID",
    "DISCORD_CHECKPOINT_CHANNEL_NAME",
    "DISCORD_DEBUG_CHANNEL_ID",
    "DISCORD_DEBUG_CHANNEL_NAME",
    "DISCORD_CONVERSATION_CHANNEL_ID",
    "DISCORD_CONVERSATION_CHANNEL_NAME",
    "DISCORD_NOTIFY_USER_ID",
)


def build_gateway_env_overrides(
    *,
    gateway_token: str,
    base_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return the env mapping the gateway child should run under.

    *gateway_token* must be the resolved
    ``ENGINEERING_AGENT_BOT_GATEWAY_TOKEN`` value. Empty / missing
    raises ``ValueError`` — the caller (``run-service`` or the
    legacy supervisor) is the right surface to report a config
    error, not silent fallback to the planning-bot token.

    *base_env* lets tests pass a controlled mapping; production
    omits it and the helper layers overrides on top of
    ``os.environ``.
    """

    if not gateway_token or not gateway_token.strip():
        raise ValueError(
            f"{GATEWAY_TOKEN_ENV} is required to start the engineering gateway"
        )

    env: dict[str, str] = (
        dict(base_env) if base_env is not None else dict(os.environ)
    )
    env["DISCORD_BOT_TOKEN"] = gateway_token.strip()
    env["DISCORD_CONVERSATION_REPLY_MODE"] = "disabled"
    for key in _PLANNING_BOT_KEYS_TO_BLANK:
        env[key] = ""
    return env


def resolve_gateway_token(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Read ``ENGINEERING_AGENT_BOT_GATEWAY_TOKEN`` from env.

    Returns ``None`` (not raise) when unset so the caller can
    decide whether to fail loudly (production) or skip silently
    (tests).
    """

    source = env if env is not None else os.environ
    raw = source.get(GATEWAY_TOKEN_ENV)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


__all__ = (
    "GATEWAY_TOKEN_ENV",
    "build_gateway_env_overrides",
    "resolve_gateway_token",
)
