"""Startup banner / warning composition (P0-Q step 6).

Extracts the boot-time message list produced by ``run_discord_bot``.
The output is the human-readable log spew the operator sees when the
bot boots: missing-env warnings, channel-overlap warnings, scheduled
briefing/checkpoint/preparation tick previews. All inputs are
``DiscordBotConfig`` + ``now`` — no Discord side-effects here.
"""

from __future__ import annotations

from datetime import datetime

from ...planning.day_profile import load_day_profile
from ..config import DiscordBotConfig
from .channels import _channel_target_text, _normalize_channel_name
from .scheduling import (
    _next_checkpoint_scan,
    _next_daily_preparation_runs,
    _next_scheduled_briefing_run,
)


def _startup_messages(config: DiscordBotConfig, *, now: datetime) -> list[str]:
    messages: list[str] = []
    daily_channel_configured = config.daily_channel_id is not None or config.daily_channel_name is not None

    messages.extend(_channel_configuration_warnings(config))
    messages.extend(_channel_overlap_warnings(config))

    if config.daily_briefing_time is not None:
        messages.append(
            "warning: DISCORD_DAILY_BRIEFING_TIME is deprecated and ignored. "
            "Planning Agent briefing schedule now follows YULE_WAKE_TIME, YULE_LUNCH_START_TIME, and YULE_WORK_END_TIME."
        )

    if daily_channel_configured:
        next_run = _next_scheduled_briefing_run(now=now, day_profile=load_day_profile(), briefing_type=None)
        messages.append(
            "info: daily briefing enabled "
            f"({_channel_target_text(config.daily_channel_id, config.daily_channel_name)}, next_run={next_run.isoformat()})"
        )
    else:
        messages.append(
            "warning: DISCORD_DAILY_CHANNEL_ID or DISCORD_DAILY_CHANNEL_NAME is missing. "
            "Scheduled daily briefings will not run."
        )

    checkpoint_channel_id = config.effective_checkpoint_channel_id
    checkpoint_channel_name = config.effective_checkpoint_channel_name
    if checkpoint_channel_id is not None or checkpoint_channel_name is not None:
        next_run = _next_checkpoint_scan(after=now)
        messages.append(
            "info: checkpoint notifications enabled "
            f"({_channel_target_text(checkpoint_channel_id, checkpoint_channel_name)}, "
            f"prefetch_minutes={config.checkpoint_prefetch_minutes}, "
            f"next_scan={next_run.isoformat()})"
        )
    else:
        messages.append("info: checkpoint notifications disabled")

    if config.notify_user_id is not None:
        messages.append(f"info: Discord notifications will mention user {config.notify_user_id}")
    else:
        messages.append("info: Discord notifications will be sent without a user mention")

    if daily_channel_configured:
        next_calendar_sync, next_github_sync, next_snapshot = _next_daily_preparation_runs(
            now=now,
            day_profile=load_day_profile(),
        )
        messages.append(
            "info: daily preparation enabled "
            f"(calendar_sync={next_calendar_sync.isoformat()}, "
            f"github_sync={next_github_sync.isoformat()}, "
            f"snapshot={next_snapshot.isoformat()})"
        )
        messages.append(
            "info: daily preparation retry policy "
            f"(retry_count={config.preparation_retry_count}, retry_delay_seconds={config.preparation_retry_delay_seconds})"
        )

    if config.effective_debug_channel_id is not None or config.effective_debug_channel_name is not None:
        messages.append(
            "info: Discord debug messages enabled "
            f"({_channel_target_text(config.effective_debug_channel_id, config.effective_debug_channel_name)})"
        )
    else:
        messages.append("info: Discord debug messages disabled")

    if config.effective_conversation_channel_id is not None or config.effective_conversation_channel_name is not None:
        messages.append(
            "info: conversation replies enabled "
            f"({_channel_target_text(config.effective_conversation_channel_id, config.effective_conversation_channel_name)}, "
            f"mode={config.conversation_reply_mode})"
        )
    elif config.conversation_reply_mode == "disabled":
        messages.append("info: conversation replies disabled")
    else:
        messages.append("info: conversation replies enabled in mention-only mode")

    return messages


def _channel_configuration_warnings(config: DiscordBotConfig) -> list[str]:
    warnings = []
    configured_channels = [
        ("DISCORD_DAILY_CHANNEL_ID", config.daily_channel_id),
        ("DISCORD_CHECKPOINT_CHANNEL_ID", config.checkpoint_channel_id),
        ("DISCORD_CONVERSATION_CHANNEL_ID", config.conversation_channel_id),
    ]
    for label, channel_id in configured_channels:
        if config.application_id is not None and channel_id is not None and channel_id == config.application_id:
            warnings.append(
                f"warning: {label} looks like DISCORD_APPLICATION_ID. "
                "Use the target Discord text channel id instead."
            )
        if channel_id is not None and channel_id == config.guild_id:
            warnings.append(
                f"warning: {label} looks like DISCORD_GUILD_ID. "
                "Use the target Discord text channel id instead."
            )
    return warnings


def _channel_overlap_warnings(config: DiscordBotConfig) -> list[str]:
    warnings: list[str] = []
    daily_id = config.daily_channel_id
    daily_name = _normalize_channel_name(config.daily_channel_name)
    conversation_id = config.effective_conversation_channel_id
    conversation_name = _normalize_channel_name(config.effective_conversation_channel_name)

    same_id = daily_id is not None and conversation_id is not None and daily_id == conversation_id
    same_name = daily_name and conversation_name and daily_name == conversation_name
    if same_id or same_name:
        warnings.append(
            "warning: daily briefing channel and conversation channel are the same. "
            "Manual chat replies can look like duplicate briefings in that channel."
        )
    return warnings
