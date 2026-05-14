from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from typing import Optional


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    application_id: Optional[int]
    guild_id: int
    daily_channel_id: Optional[int] = None
    daily_channel_name: Optional[str] = None
    debug_channel_id: Optional[int] = None
    debug_channel_name: Optional[str] = None
    checkpoint_channel_id: Optional[int] = None
    checkpoint_channel_name: Optional[str] = None
    conversation_channel_id: Optional[int] = None
    conversation_channel_name: Optional[str] = None
    conversation_reply_mode: str = "mention-only"
    notify_user_id: Optional[int] = None
    daily_briefing_time: Optional[time] = None
    checkpoint_prefetch_minutes: int = 5
    preparation_retry_count: int = 2
    preparation_retry_delay_seconds: int = 15

    @property
    def effective_checkpoint_channel_id(self) -> Optional[int]:
        return self.checkpoint_channel_id or self.daily_channel_id

    @property
    def effective_checkpoint_channel_name(self) -> Optional[str]:
        return self.checkpoint_channel_name or self.daily_channel_name

    @property
    def effective_conversation_channel_id(self) -> Optional[int]:
        return self.conversation_channel_id or self.daily_channel_id

    @property
    def effective_conversation_channel_name(self) -> Optional[str]:
        return self.conversation_channel_name or self.daily_channel_name

    @property
    def effective_debug_channel_id(self) -> Optional[int]:
        return self.debug_channel_id

    @property
    def effective_debug_channel_name(self) -> Optional[str]:
        return self.debug_channel_name

    @classmethod
    def from_env(cls) -> "DiscordBotConfig":
        return cls(
            token=_required_env("DISCORD_BOT_TOKEN"),
            application_id=_optional_int_env("DISCORD_APPLICATION_ID"),
            guild_id=_required_int_env("DISCORD_GUILD_ID"),
            daily_channel_id=_optional_int_env("DISCORD_DAILY_CHANNEL_ID"),
            daily_channel_name=_optional_string_env("DISCORD_DAILY_CHANNEL_NAME"),
            debug_channel_id=_optional_int_env("DISCORD_DEBUG_CHANNEL_ID"),
            debug_channel_name=_optional_string_env("DISCORD_DEBUG_CHANNEL_NAME"),
            checkpoint_channel_id=_optional_int_env("DISCORD_CHECKPOINT_CHANNEL_ID"),
            checkpoint_channel_name=_optional_string_env("DISCORD_CHECKPOINT_CHANNEL_NAME"),
            conversation_channel_id=_optional_int_env("DISCORD_CONVERSATION_CHANNEL_ID"),
            conversation_channel_name=_optional_string_env("DISCORD_CONVERSATION_CHANNEL_NAME"),
            conversation_reply_mode=_conversation_reply_mode_env("DISCORD_CONVERSATION_REPLY_MODE"),
            notify_user_id=_optional_int_env("DISCORD_NOTIFY_USER_ID"),
            daily_briefing_time=_optional_time_env("DISCORD_DAILY_BRIEFING_TIME"),
            checkpoint_prefetch_minutes=_optional_positive_int_env(
                "DISCORD_CHECKPOINT_PREFETCH_MINUTES",
                default=5,
            ),
            preparation_retry_count=_optional_non_negative_int_env(
                "DISCORD_PREPARATION_RETRY_COUNT",
                default=2,
            ),
            preparation_retry_delay_seconds=_optional_positive_int_env(
                "DISCORD_PREPARATION_RETRY_DELAY_SECONDS",
                default=15,
            ),
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required. Add it to .env.local before running the Discord bot.")
    return value


def _required_int_env(name: str) -> int:
    value = _required_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc


def _optional_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc


def _optional_string_env(name: str) -> Optional[str]:
    raw = os.environ.get(name)
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None
    return value


def _optional_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc

    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0, got: {value!r}")

    return parsed


def _optional_non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer value, got: {value!r}") from exc

    if parsed < 0:
        raise ValueError(f"{name} must be 0 or greater, got: {value!r}")

    return parsed


def _optional_time_env(name: str) -> Optional[time]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None

    value = raw.strip()
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"{name} must use HH:MM format, got: {value!r}")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"{name} must use HH:MM format, got: {value!r}") from exc

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"{name} must use a valid 24-hour time, got: {value!r}")

    return time(hour=hour, minute=minute)


def _conversation_reply_mode_env(name: str) -> str:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return "mention-only"

    allowed = {"mention-only", "plain-message-or-mention", "disabled"}
    if raw not in allowed:
        raise ValueError(
            f"{name} must be one of {sorted(allowed)}, got: {raw!r}"
        )
    return raw
