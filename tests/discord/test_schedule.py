from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

import asyncio
from datetime import datetime, time, timedelta
import unittest
from unittest.mock import patch

from yule_orchestrator.discord.bot import (
    _collect_due_daily_preparation_steps,
    _daily_preparation_schedule_for,
    _next_daily_preparation_runs,
    _next_scheduled_briefing_run,
    _resolve_messageable_channel,
    _startup_messages,
)
from yule_orchestrator.discord.config import DiscordBotConfig
from yule_orchestrator.planning.day_profile import DayProfile


class DiscordScheduleTestCase(unittest.TestCase):
    def test_next_scheduled_briefing_run_returns_same_day_when_future_time(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T15:10:00+09:00")
        day_profile = _day_profile()
        next_run = _next_scheduled_briefing_run(now=fake_now, day_profile=day_profile, briefing_type="evening")
        self.assertEqual(next_run, fake_now.replace(hour=18, minute=0, second=0, microsecond=0))

    def test_next_scheduled_briefing_run_rolls_to_next_day_when_time_passed(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T18:20:00+09:00")
        day_profile = _day_profile()
        next_run = _next_scheduled_briefing_run(now=fake_now, day_profile=day_profile, briefing_type="evening")
        expected = fake_now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)
        self.assertEqual(next_run, expected)

    def test_next_daily_preparation_runs_are_offset_from_briefing(self) -> None:
        now = datetime.fromisoformat("2026-04-22T05:40:00+09:00")

        calendar_sync, github_sync, snapshot = _next_daily_preparation_runs(
            now=now,
            day_profile=_day_profile(),
        )

        self.assertEqual(calendar_sync, datetime.fromisoformat("2026-04-22T05:50:00+09:00"))
        self.assertEqual(github_sync, datetime.fromisoformat("2026-04-22T05:55:00+09:00"))
        self.assertEqual(snapshot, datetime.fromisoformat("2026-04-22T05:58:00+09:00"))

    def test_collect_due_daily_preparation_steps_returns_steps_in_order(self) -> None:
        last_scan = datetime.fromisoformat("2026-04-22T05:49:30+09:00")
        scan_time = datetime.fromisoformat("2026-04-22T05:58:30+09:00")

        due_steps = _collect_due_daily_preparation_steps(
            last_scan=last_scan,
            scan_time=scan_time,
            day_profile=_day_profile(),
            completed_steps=set(),
        )

        self.assertEqual(
            [(step_name, plan_date.isoformat(), scheduled_at.isoformat()) for step_name, plan_date, scheduled_at in due_steps],
            [
                ("calendar_sync", "2026-04-22", "2026-04-22T05:50:00+09:00"),
                ("github_sync", "2026-04-22", "2026-04-22T05:55:00+09:00"),
                ("planning_snapshot", "2026-04-22", "2026-04-22T05:58:00+09:00"),
            ],
        )

    def test_startup_messages_warn_when_daily_channel_is_missing(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T16:20:00+09:00")
        config = DiscordBotConfig(
            token="token",
            application_id=None,
            guild_id=123,
            daily_channel_id=None,
            daily_channel_name=None,
            checkpoint_channel_id=None,
            checkpoint_channel_name=None,
            conversation_channel_id=None,
            conversation_channel_name=None,
            notify_user_id=None,
            daily_briefing_time=None,
            checkpoint_prefetch_minutes=5,
        )

        messages = _startup_messages(config, now=fake_now)

        self.assertTrue(
            any(
                "DISCORD_DAILY_CHANNEL_ID or DISCORD_DAILY_CHANNEL_NAME is missing"
                in message
                for message in messages
            )
        )
        self.assertIn("info: checkpoint notifications disabled", messages)

    def test_startup_messages_describe_enabled_schedules(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T16:20:00+09:00")
        config = DiscordBotConfig(
            token="token",
            application_id=None,
            guild_id=123,
            daily_channel_id=456,
            daily_channel_name="planning",
            checkpoint_channel_id=789,
            checkpoint_channel_name="checkpoints",
            conversation_channel_id=654,
            conversation_channel_name="planning-chat",
            conversation_reply_mode="plain-message-or-mention",
            notify_user_id=999,
            daily_briefing_time=None,
            checkpoint_prefetch_minutes=7,
        )

        messages = _startup_messages(config, now=fake_now)

        self.assertTrue(any("daily briefing enabled" in message for message in messages))
        self.assertTrue(any("channel_id=456" in message for message in messages))
        self.assertTrue(any("checkpoint notifications enabled" in message for message in messages))
        self.assertTrue(any("channel_id=789" in message for message in messages))
        self.assertIn("info: Discord notifications will mention user 999", messages)
        self.assertTrue(any("daily preparation enabled" in message for message in messages))
        self.assertTrue(any("daily preparation retry policy" in message for message in messages))
        self.assertIn(
            "info: conversation replies enabled (channel_id=654, channel_name=planning-chat, mode=plain-message-or-mention)",
            messages,
        )
        self.assertIn("info: Discord debug messages disabled", messages)

    def test_startup_messages_warn_when_daily_and_conversation_channels_overlap(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T16:20:00+09:00")
        config = DiscordBotConfig(
            token="token",
            application_id=None,
            guild_id=123,
            daily_channel_id=456,
            daily_channel_name="planning",
            checkpoint_channel_id=None,
            checkpoint_channel_name=None,
            conversation_channel_id=456,
            conversation_channel_name="planning",
            conversation_reply_mode="plain-message-or-mention",
            notify_user_id=None,
            daily_briefing_time=None,
            checkpoint_prefetch_minutes=5,
        )

        messages = _startup_messages(config, now=fake_now)

        self.assertTrue(
            any("daily briefing channel and conversation channel are the same" in message for message in messages)
        )

    def test_startup_messages_warn_when_channel_id_looks_like_application_id(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T16:20:00+09:00")
        config = DiscordBotConfig(
            token="token",
            application_id=456,
            guild_id=123,
            daily_channel_id=456,
            daily_channel_name=None,
            checkpoint_channel_id=None,
            checkpoint_channel_name=None,
            conversation_channel_id=None,
            conversation_channel_name=None,
            notify_user_id=None,
            daily_briefing_time=None,
            checkpoint_prefetch_minutes=5,
        )

        messages = _startup_messages(config, now=fake_now)

        self.assertTrue(
            any("DISCORD_DAILY_CHANNEL_ID looks like DISCORD_APPLICATION_ID" in message for message in messages)
        )

    def test_startup_messages_warn_when_channel_id_looks_like_guild_id(self) -> None:
        fake_now = datetime.fromisoformat("2026-04-22T16:20:00+09:00")
        config = DiscordBotConfig(
            token="token",
            application_id=None,
            guild_id=123,
            daily_channel_id=123,
            daily_channel_name=None,
            checkpoint_channel_id=None,
            checkpoint_channel_name=None,
            conversation_channel_id=None,
            conversation_channel_name=None,
            notify_user_id=None,
            daily_briefing_time=None,
            checkpoint_prefetch_minutes=5,
        )

        messages = _startup_messages(config, now=fake_now)

        self.assertTrue(
            any("DISCORD_DAILY_CHANNEL_ID looks like DISCORD_GUILD_ID" in message for message in messages)
        )

    def test_resolve_messageable_channel_falls_back_to_name(self) -> None:
        class FakeMessageable:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        class FakeGuild:
            def __init__(self, channels) -> None:
                self.channels = channels

        class FakeBot:
            def __init__(self, channels) -> None:
                self._channels = channels
                self._guild = FakeGuild(channels)

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                raise RuntimeError("missing")

            def get_guild(self, guild_id):
                return self._guild

            def get_all_channels(self):
                return self._channels

        class FakeDiscordModule:
            class abc:
                Messageable = FakeMessageable

        async def run_case():
            return await _resolve_messageable_channel(
                FakeBot([FakeMessageable(999, "planning-debug")]),
                guild_id=123,
                channel_id=456,
                channel_name="planning-debug",
                discord_module=FakeDiscordModule,
                error_label="DISCORD_DEBUG_CHANNEL_ID",
            )

        resolved = asyncio.run(run_case())
        self.assertEqual(resolved.id, 999)


def _day_profile() -> DayProfile:
    return DayProfile(
        wake_time=time(6, 0),
        work_start_time=time(9, 0),
        lunch_start_time=time(13, 0),
        work_end_time=time(18, 0),
        commute_minutes=45,
        departure_buffer_minutes=10,
        home_area="신정동",
        work_area="마곡",
    )
