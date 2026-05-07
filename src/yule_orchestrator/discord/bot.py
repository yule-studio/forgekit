from __future__ import annotations

import asyncio
import json
import math
import os
import time as time_module
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

from ..agents import (
    Dispatcher,
    WorkflowOrchestrator,
    build_participants_pool,
)
from ..agents.workflow_state import (
    find_latest_open_session,
    list_sessions as workflow_list_sessions,
    load_session,
    update_session,
)
from ..integrations.calendar import list_naver_calendar_items
from ..integrations.calendar.models import build_fallback_item_uid
from ..integrations.github.issues import list_open_issues
from ..integrations.github.pulls import list_open_pull_requests
from ..observability import RuntimeStepMetric, save_runtime_metric_run
from ..planning import build_daily_plan, collect_planning_inputs, load_reminder_items, save_daily_plan_snapshot
from ..planning.day_profile import DayProfile, DayProfileBriefingSlot, load_day_profile
from ..planning.models import PlanningCheckpoint, PlanningScheduledBriefing
from ..storage import load_json_cache, save_json_cache
from .checkpoint_state import (
    filter_unresponded_checkpoints,
    save_checkpoint_pending_response,
)
from .commands import register_discord_commands
from .conversation import build_conversation_response_envelope
from .config import DiscordBotConfig
from .engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadKickoff,
    EngineeringThreadContinuation,
    route_engineering_message,
    should_continue_existing_thread,
    should_start_new_thread,
)
from .research_forum import (
    FORUM_STARTER_CONTENT_LIMIT,
    ResearchForumContext,
    chunk_for_discord_message,
    truncate_for_starter_message,
)
from .typing_indicator import (
    typing_context,
    typing_keepalive,
    wrap_send_chunks_with_typing,
)
from ..agents.research.loop import (
    publish_research_loop_to_forum,
    run_research_loop,
)
from ..agents.research.collector import resolve_forum_comment_mode
from ..agents.deliberation import synthesis_to_dict
from ..agents.research.pack import pack_to_dict
from ..agents.research.persistence import persist_research_artifacts
from ..agents.research.profiles import format_research_hints_block
from .engineering_team_runtime import kickoff_directive
from .formatter import (
    format_checkpoints_message,
    format_plan_today_message,
    format_scheduled_briefing_message,
    format_snapshot_regenerating_message,
    format_snapshot_regeneration_failed_message,
    split_discord_message,
)
from .planning_runtime import build_due_checkpoints, load_plan_today_snapshot
from .planning_runtime import build_due_briefings, load_prefetched_due_checkpoints, prefetch_checkpoint_snapshots
from .snapshot_refresh import regenerate_today_snapshot

CHECKPOINT_NOTIFICATION_NAMESPACE = "discord-checkpoint-notifications"
BRIEFING_NOTIFICATION_NAMESPACE = "discord-scheduled-briefings"
CHECKPOINT_NOTIFICATION_TTL_SECONDS = 2 * 24 * 60 * 60
BRIEFING_NOTIFICATION_TTL_SECONDS = 2 * 24 * 60 * 60
DAILY_PREPARATION_GITHUB_LIMIT = 30
DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES = 10
DAILY_PREPARATION_GITHUB_OFFSET_MINUTES = 5
DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES = 2


def run_discord_bot(repo_root: Path) -> None:
    import discord
    from discord.ext import commands

    config = DiscordBotConfig.from_env()
    day_profile = load_day_profile()

    class YuleDiscordBot(commands.Bot):
        def __init__(self) -> None:
            intents = discord.Intents.default()
            intents.message_content = True
            intents.messages = True
            self._daily_briefing_task: asyncio.Task[None] | None = None
            self._daily_preparation_task: asyncio.Task[None] | None = None
            self._checkpoint_notification_task: asyncio.Task[None] | None = None
            self._checkpoint_prefetch_task: asyncio.Task[None] | None = None
            self._checkpoint_storage_lock: asyncio.Lock | None = None
            self._completed_preparation_steps: set[tuple[str, str]] = set()
            self._daily_preparation_context: dict[str, dict[str, object]] = {}
            self._snapshot_refresh_locks: dict[str, asyncio.Lock] = {}
            super().__init__(
                command_prefix=commands.when_mentioned,
                intents=intents,
            )
            _set_active_discord_bot(self)

        async def setup_hook(self) -> None:
            actual_application_id = self.application_id
            if (
                config.application_id is not None
                and actual_application_id is not None
                and config.application_id != actual_application_id
            ):
                print(
                    "warning: DISCORD_APPLICATION_ID does not match the bot token's application. "
                    f"configured={config.application_id}, actual={actual_application_id}. "
                    "The token-linked application will be used."
                )
            register_discord_commands(
                self,
                guild_id=config.guild_id,
                notify_user_id=config.notify_user_id,
            )
            guild = discord.Object(id=config.guild_id)
            await self.tree.sync(guild=guild)
            self._checkpoint_storage_lock = asyncio.Lock()
            daily_channel_configured = config.daily_channel_id is not None or config.daily_channel_name is not None
            if daily_channel_configured:
                self._daily_preparation_task = asyncio.create_task(self._run_daily_preparation_loop())
            if daily_channel_configured:
                self._daily_briefing_task = asyncio.create_task(self._run_daily_briefing_loop())
            if (
                config.effective_checkpoint_channel_id is not None
                or config.effective_checkpoint_channel_name is not None
            ):
                self._checkpoint_prefetch_task = asyncio.create_task(self._run_checkpoint_prefetch_loop())
                self._checkpoint_notification_task = asyncio.create_task(
                    self._run_checkpoint_notification_loop()
                )

        async def on_ready(self) -> None:
            user_text = str(self.user) if self.user is not None else "unknown-user"
            print(f"Discord bot logged in as {user_text} (guild={config.guild_id})")
            for message in _startup_messages(config, now=datetime.now().astimezone()):
                print(message)

        async def on_message(self, message: "discord.Message") -> None:
            if message.author.bot:
                return
            if message.guild is None or message.guild.id != config.guild_id:
                return
            if self.user is None:
                return

            content_text = str(getattr(message, "content", "") or "").strip()
            if content_text.startswith("/"):
                return

            engineering_context = EngineeringRouteContext.from_env()
            if engineering_context.configured:
                # Phase 1 fix: don't open a typing context around the
                # whole route. Doing so showed "입력 중..." even when the
                # router ultimately returned ``handled=False`` (non-
                # engineering channel, ignored phrase, member-bot scope
                # mismatch) — the indicator stopped being a real
                # response signal. Instead, wrap ``send_chunks`` so the
                # typing indicator fires only during the actual chunk
                # send. That keeps the "bot is composing" cue visible
                # whenever the gateway commits a response, and silent
                # the rest of the time.
                send_chunks = wrap_send_chunks_with_typing(
                    _make_engineering_send_chunks(discord)
                )
                engineering_result = await route_engineering_message(
                    message=message,
                    bot_user=self.user,
                    route_context=engineering_context,
                    extract_prompt=_extract_conversation_prompt,
                    conversation_fn=_default_engineering_conversation_fn,
                    intake_fn=_default_engineering_intake_fn,
                    thread_kickoff_fn=_make_default_thread_kickoff_fn(discord),
                    send_chunks=send_chunks,
                    research_loop_fn=_make_default_engineering_research_loop_fn(discord),
                    thread_continuation_fn=_make_default_thread_continuation_fn(discord),
                    # Phase 4 — runtime preflight uses the live
                    # workflow session store so "어제 작업 이어서
                    # 요약해줘" et al. never reach
                    # auto_collect=True. Disabled flag-style by
                    # tests that inject their own routing seam.
                    list_sessions_fn=workflow_list_sessions,
                )
                if engineering_result.handled:
                    return

            if not _should_handle_message(
                message=message,
                bot_user=self.user,
                conversation_channel_id=config.effective_conversation_channel_id,
                conversation_channel_name=config.effective_conversation_channel_name,
                conversation_reply_mode=config.conversation_reply_mode,
                daily_channel_id=config.daily_channel_id,
                daily_channel_name=config.daily_channel_name,
            ):
                return

            prompt = _extract_conversation_prompt(message=message, bot_user=self.user).strip()
            if not prompt:
                prompt = "오늘 뭐부터 해야 해?"

            mention_user = _message_mentions_bot(message=message, bot_user=self.user)
            conversation_scope = (
                f"guild:{config.guild_id}:channel:{getattr(message.channel, 'id', 'unknown')}"
            )

            async with message.channel.typing():
                envelope = await asyncio.to_thread(
                    build_conversation_response_envelope,
                    prompt,
                    author_user_id=message.author.id,
                    conversation_scope=conversation_scope,
                    mention_user=mention_user,
                )

            await _send_channel_message_chunks(
                message.channel,
                envelope.content,
                allowed_mentions=_build_allowed_mentions(discord),
            )

            if envelope.regenerate_snapshot:
                asyncio.create_task(
                    self._regenerate_snapshot_and_followup(
                        channel=message.channel,
                        prompt=prompt,
                        author_user_id=message.author.id,
                        conversation_scope=conversation_scope,
                        mention_user=mention_user,
                        mention_user_id=envelope.mention_user_id,
                        discord_module=discord,
                    )
                )

        async def close(self) -> None:
            await _cancel_task(self._daily_preparation_task)
            await _cancel_task(self._daily_briefing_task)
            await _cancel_task(self._checkpoint_prefetch_task)
            await _cancel_task(self._checkpoint_notification_task)
            await super().close()

        async def ensure_snapshot(self, plan_date: date) -> tuple[object | None, str | None]:
            lock = self._snapshot_refresh_locks.setdefault(plan_date.isoformat(), asyncio.Lock())
            async with lock:
                snapshot = await asyncio.to_thread(load_plan_today_snapshot, plan_date)
                if snapshot is not None:
                    return snapshot, None
                result = await asyncio.to_thread(regenerate_today_snapshot, plan_date)
                if not result.ok:
                    return None, result.error
                snapshot = await asyncio.to_thread(load_plan_today_snapshot, plan_date)
                if snapshot is None:
                    return None, "snapshot 재생성 직후에도 snapshot을 다시 읽지 못했습니다."
                return snapshot, None

        async def _regenerate_snapshot_and_followup(
            self,
            *,
            channel: "discord.abc.Messageable",
            prompt: str,
            author_user_id: int,
            conversation_scope: str,
            mention_user: bool,
            mention_user_id: int | None,
            discord_module: "discord",
        ) -> None:
            plan_date = datetime.now().astimezone().date()
            snapshot, error = await self.ensure_snapshot(plan_date)
            if snapshot is None:
                await _send_channel_message_chunks(
                    channel,
                    format_snapshot_regeneration_failed_message(
                        mention_user_id=mention_user_id,
                        error=error,
                    ),
                    allowed_mentions=_build_allowed_mentions(discord_module),
                )
                return

            followup = await asyncio.to_thread(
                build_conversation_response_envelope,
                prompt,
                author_user_id=author_user_id,
                conversation_scope=conversation_scope,
                mention_user=mention_user,
            )

            await _send_channel_message_chunks(
                channel,
                followup.content,
                allowed_mentions=_build_allowed_mentions(discord_module),
            )

        async def _run_daily_preparation_loop(self) -> None:
            await self.wait_until_ready()
            last_scan = datetime.now().astimezone()
            while not self.is_closed():
                next_run = _next_checkpoint_scan()
                wait_seconds = max(1.0, (next_run - datetime.now().astimezone()).total_seconds())
                await asyncio.sleep(wait_seconds)
                scan_time = datetime.now().astimezone()
                due_steps = _collect_due_daily_preparation_steps(
                    last_scan=last_scan,
                    scan_time=scan_time,
                    day_profile=day_profile,
                    completed_steps=self._completed_preparation_steps,
                )
                for step_name, plan_date, scheduled_at in due_steps:
                    try:
                        await self._run_daily_preparation_step_with_retry(
                            step_name=step_name,
                            plan_date=plan_date,
                            scheduled_at=scheduled_at,
                        )
                        self._completed_preparation_steps.add((plan_date.isoformat(), step_name))
                    except Exception as exc:
                        _log_preparation_event(
                            level="warning",
                            event="step_failed",
                            step_name=step_name,
                            plan_date=plan_date.isoformat(),
                            scheduled_at=scheduled_at.isoformat(),
                            ok=False,
                            error=str(exc),
                        )
                _cleanup_completed_preparation_steps(
                    self._completed_preparation_steps,
                    today=scan_time.date(),
                )
                _cleanup_preparation_context(
                    self._daily_preparation_context,
                    today=scan_time.date(),
                )
                last_scan = scan_time

        async def _run_daily_preparation_step_with_retry(
            self,
            *,
            step_name: str,
            plan_date: date,
            scheduled_at: datetime,
        ) -> None:
            attempt_limit = max(1, config.preparation_retry_count + 1)
            last_error: Exception | None = None
            for attempt in range(1, attempt_limit + 1):
                attempt_started_at = datetime.now().astimezone()
                attempt_started_perf = time_module.perf_counter()
                _log_preparation_event(
                    level="info",
                    event="step_started",
                    step_name=step_name,
                    plan_date=plan_date.isoformat(),
                    scheduled_at=scheduled_at.isoformat(),
                    attempt=attempt,
                    attempt_limit=attempt_limit,
                )
                try:
                    result_metadata = await self._run_daily_preparation_step(
                        step_name=step_name,
                        plan_date=plan_date,
                    )
                    duration_seconds = time_module.perf_counter() - attempt_started_perf
                    _save_preparation_metric(
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        started_at=attempt_started_at,
                        duration_seconds=duration_seconds,
                        ok=True,
                        metadata={
                            "scheduled_at": scheduled_at.isoformat(),
                            "attempt": attempt,
                            "attempt_limit": attempt_limit,
                            **result_metadata,
                        },
                    )
                    _log_preparation_event(
                        level="info",
                        event="step_completed",
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        scheduled_at=scheduled_at.isoformat(),
                        attempt=attempt,
                        attempt_limit=attempt_limit,
                        ok=True,
                        duration_seconds=round(duration_seconds, 3),
                        metadata=result_metadata,
                    )
                    await self._send_preparation_debug_message(
                        level="info",
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        scheduled_at=scheduled_at.isoformat(),
                        attempt=attempt,
                        attempt_limit=attempt_limit,
                        ok=True,
                        duration_seconds=round(duration_seconds, 3),
                        metadata=result_metadata,
                    )
                    return
                except Exception as exc:
                    last_error = exc
                    duration_seconds = time_module.perf_counter() - attempt_started_perf
                    _save_preparation_metric(
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        started_at=attempt_started_at,
                        duration_seconds=duration_seconds,
                        ok=False,
                        metadata={
                            "scheduled_at": scheduled_at.isoformat(),
                            "attempt": attempt,
                            "attempt_limit": attempt_limit,
                        },
                        error=str(exc),
                    )
                    retry_delay_seconds = config.preparation_retry_delay_seconds
                    retry_scheduled = attempt < attempt_limit
                    _log_preparation_event(
                        level="warning",
                        event="step_attempt_failed",
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        scheduled_at=scheduled_at.isoformat(),
                        attempt=attempt,
                        attempt_limit=attempt_limit,
                        ok=False,
                        duration_seconds=round(duration_seconds, 3),
                        retry_scheduled=retry_scheduled,
                        retry_delay_seconds=retry_delay_seconds if retry_scheduled else 0,
                        error=str(exc),
                    )
                    await self._send_preparation_debug_message(
                        level="warning",
                        step_name=step_name,
                        plan_date=plan_date.isoformat(),
                        scheduled_at=scheduled_at.isoformat(),
                        attempt=attempt,
                        attempt_limit=attempt_limit,
                        ok=False,
                        duration_seconds=round(duration_seconds, 3),
                        retry_scheduled=retry_scheduled,
                        retry_delay_seconds=retry_delay_seconds if retry_scheduled else 0,
                        error=str(exc),
                    )
                    if retry_scheduled:
                        await asyncio.sleep(retry_delay_seconds)
                        continue
                    break

            if last_error is not None:
                raise last_error

        async def _run_daily_preparation_step(
            self,
            *,
            step_name: str,
            plan_date: date,
        ) -> dict[str, object]:
            context = self._daily_preparation_context.setdefault(plan_date.isoformat(), {})
            if step_name == "calendar_sync":
                result = await asyncio.to_thread(
                    list_naver_calendar_items,
                    plan_date,
                    plan_date,
                )
                context["calendar_result"] = result
                return {
                    "event_count": len(result.events),
                    "todo_count": len(result.todos),
                }

            if step_name == "github_sync":
                issues = await asyncio.to_thread(
                    list_open_issues,
                    DAILY_PREPARATION_GITHUB_LIMIT,
                )
                context["github_issues"] = list(issues)
                pulls: list = []
                try:
                    fetched_pulls = await asyncio.to_thread(
                        list_open_pull_requests,
                        DAILY_PREPARATION_GITHUB_LIMIT,
                    )
                    pulls = list(fetched_pulls)
                except Exception as exc:
                    print(f"warning: github pulls fetch failed during daily preparation: {exc}")
                context["github_pull_requests"] = pulls
                return {
                    "issue_count": len(issues),
                    "pull_request_count": len(pulls),
                }

            if step_name == "planning_snapshot":
                reminders = await asyncio.to_thread(load_reminder_items, None)
                prefetched_calendar_result = context.get("calendar_result")
                if prefetched_calendar_result is not None and not hasattr(prefetched_calendar_result, "events"):
                    prefetched_calendar_result = None
                prefetched_github_issues = context.get("github_issues")
                if prefetched_github_issues is not None and not isinstance(prefetched_github_issues, list):
                    prefetched_github_issues = None
                prefetched_github_pull_requests = context.get("github_pull_requests")
                if prefetched_github_pull_requests is not None and not isinstance(prefetched_github_pull_requests, list):
                    prefetched_github_pull_requests = None
                inputs = await asyncio.to_thread(
                    collect_planning_inputs,
                    plan_date,
                    github_limit=DAILY_PREPARATION_GITHUB_LIMIT,
                    include_calendar=True,
                    include_github=True,
                    reminders=reminders,
                    prefetched_calendar_result=prefetched_calendar_result,
                    prefetched_github_issues=prefetched_github_issues,
                    prefetched_github_pull_requests=prefetched_github_pull_requests,
                    allow_live_calendar_fetch=prefetched_calendar_result is None,
                    allow_live_github_fetch=prefetched_github_issues is None,
                )
                envelope = await asyncio.to_thread(build_daily_plan, inputs)
                await asyncio.to_thread(save_daily_plan_snapshot, envelope)
                return {
                    "recommended_task_count": envelope.daily_plan.summary.recommended_task_count,
                    "checkpoint_count": len(envelope.daily_plan.checkpoints),
                    "warning_count": len(inputs.warnings),
                    "calendar_source": _preparation_source_label(inputs.source_statuses, "calendar"),
                    "github_source": _preparation_source_label(inputs.source_statuses, "github"),
                }

            raise ValueError(f"Unsupported daily preparation step: {step_name}")

        async def _send_preparation_debug_message(
            self,
            *,
            level: str,
            step_name: str,
            plan_date: str,
            scheduled_at: str,
            attempt: int,
            attempt_limit: int,
            ok: bool,
            duration_seconds: float,
            metadata: dict[str, object] | None = None,
            retry_scheduled: bool = False,
            retry_delay_seconds: int = 0,
            error: str | None = None,
        ) -> None:
            debug_channel_id = config.effective_debug_channel_id
            debug_channel_name = config.effective_debug_channel_name
            if debug_channel_id is None and debug_channel_name is None:
                return

            try:
                channel = await _resolve_messageable_channel(
                    self,
                    guild_id=config.guild_id,
                    channel_id=debug_channel_id,
                    channel_name=debug_channel_name,
                    discord_module=discord,
                    error_label="DISCORD_DEBUG_CHANNEL_ID",
                )
            except Exception as exc:
                print(f"warning: failed to resolve Discord debug channel: {exc}")
                return

            lines = [
                f"[daily-preparation] {step_name}",
                f"- level: {level}",
                f"- plan_date: {plan_date}",
                f"- scheduled_at: {scheduled_at}",
                f"- attempt: {attempt}/{attempt_limit}",
                f"- ok: {'true' if ok else 'false'}",
                f"- duration_seconds: {duration_seconds:.3f}",
            ]
            if retry_scheduled:
                lines.append(f"- retry_in_seconds: {retry_delay_seconds}")
            if metadata:
                lines.append(f"- metadata: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}")
            if error:
                lines.append(f"- error: {error}")
            await _send_channel_message_chunks(
                channel,
                "\n".join(lines),
                allowed_mentions=_build_allowed_mentions(discord),
            )

        async def _run_daily_briefing_loop(self) -> None:
            await self.wait_until_ready()
            last_scan = datetime.now().astimezone()
            while not self.is_closed():
                next_run = _next_checkpoint_scan()
                wait_seconds = max(1.0, (next_run - datetime.now().astimezone()).total_seconds())
                await asyncio.sleep(wait_seconds)
                scan_time = datetime.now().astimezone()
                try:
                    await self._send_due_briefings(last_scan=last_scan, scan_time=scan_time)
                except Exception as exc:
                    print(f"warning: failed to send scheduled daily briefing: {exc}")
                last_scan = scan_time

        async def _send_due_briefings(
            self,
            *,
            last_scan: datetime,
            scan_time: datetime,
        ) -> None:
            if config.daily_channel_id is None and config.daily_channel_name is None:
                return
            if scan_time <= last_scan:
                return

            channel = await _resolve_messageable_channel(
                self,
                guild_id=config.guild_id,
                channel_id=config.daily_channel_id,
                channel_name=config.daily_channel_name,
                discord_module=discord,
                error_label="DISCORD_DAILY_CHANNEL_ID",
            )
            resolved_channel_id = getattr(channel, "id", None) or config.daily_channel_id
            due_slots = _collect_due_briefing_slots(
                last_scan=last_scan,
                scan_time=scan_time,
                day_profile=day_profile,
            )

            for slot, plan_date in due_slots:
                briefing = _synthesize_scheduled_briefing(slot, plan_date)
                async with self._checkpoint_lock():
                    already_sent = await asyncio.to_thread(
                        _has_briefing_been_sent_async,
                        resolved_channel_id,
                        briefing.briefing_id,
                    )
                if already_sent:
                    continue

                snapshot = await asyncio.to_thread(load_plan_today_snapshot, plan_date)
                if snapshot is None:
                    ack = format_snapshot_regenerating_message(
                        mention_user_id=config.notify_user_id,
                        slot_title=slot.title,
                    )
                    try:
                        await _send_channel_message_chunks(
                            channel,
                            ack,
                            allowed_mentions=_build_allowed_mentions(discord),
                        )
                    except Exception as exc:
                        print(f"warning: failed to send scheduled briefing ack: {exc}")
                        continue

                    snapshot, error = await self.ensure_snapshot(plan_date)
                    if snapshot is None:
                        fail = format_snapshot_regeneration_failed_message(
                            mention_user_id=config.notify_user_id,
                            error=error,
                        )
                        try:
                            await _send_channel_message_chunks(
                                channel,
                                fail,
                                allowed_mentions=_build_allowed_mentions(discord),
                            )
                        except Exception as exc:
                            print(f"warning: failed to send scheduled briefing failure: {exc}")
                        continue

                content = format_scheduled_briefing_message(
                    briefing,
                    mention_user_id=config.notify_user_id,
                    snapshot=snapshot,
                )
                send_started_at = datetime.now().astimezone()
                send_started = time_module.perf_counter()
                try:
                    await _send_channel_message_chunks(
                        channel,
                        content,
                        allowed_mentions=_build_allowed_mentions(discord),
                    )
                except Exception as exc:
                    _save_discord_send_metric(
                        workflow="discord-daily-briefing",
                        started_at=send_started_at,
                        duration_seconds=time_module.perf_counter() - send_started,
                        ok=False,
                        channel_id=resolved_channel_id,
                        message_count=len(split_discord_message(content)),
                        snapshot_state=_snapshot_state_label(snapshot),
                        error=str(exc),
                    )
                    raise

                _save_discord_send_metric(
                    workflow="discord-daily-briefing",
                    started_at=send_started_at,
                    duration_seconds=time_module.perf_counter() - send_started,
                    ok=True,
                    channel_id=resolved_channel_id,
                    message_count=len(split_discord_message(content)),
                    snapshot_state=_snapshot_state_label(snapshot),
                )
                async with self._checkpoint_lock():
                    await asyncio.to_thread(
                        _mark_briefings_sent,
                        resolved_channel_id,
                        [briefing],
                    )

        async def _run_checkpoint_notification_loop(self) -> None:
            await self.wait_until_ready()
            last_scan = datetime.now().astimezone()
            while not self.is_closed():
                next_run = _next_checkpoint_scan()
                wait_seconds = max(1.0, (next_run - datetime.now().astimezone()).total_seconds())
                await asyncio.sleep(wait_seconds)
                scan_time = datetime.now().astimezone()
                try:
                    await self._send_due_checkpoints(last_scan=last_scan, scan_time=scan_time)
                except Exception as exc:
                    print(f"warning: failed to send checkpoint notifications: {exc}")
                last_scan = scan_time

        async def _run_checkpoint_prefetch_loop(self) -> None:
            await self.wait_until_ready()
            while not self.is_closed():
                started_at = datetime.now().astimezone()
                try:
                    async with self._checkpoint_lock():
                        await asyncio.to_thread(
                            prefetch_checkpoint_snapshots,
                            started_at,
                            prefetch_minutes=config.checkpoint_prefetch_minutes,
                        )
                except Exception as exc:
                    print(f"warning: failed to prefetch checkpoint snapshots: {exc}")

                next_run = _next_checkpoint_scan(after=started_at)
                wait_seconds = max(1.0, (next_run - datetime.now().astimezone()).total_seconds())
                await asyncio.sleep(wait_seconds)

        async def _send_due_checkpoints(
            self,
            *,
            last_scan: datetime,
            scan_time: datetime,
        ) -> None:
            channel_id = config.effective_checkpoint_channel_id
            channel_name = config.effective_checkpoint_channel_name
            if channel_id is None and channel_name is None:
                return
            if scan_time <= last_scan:
                return

            channel = await _resolve_messageable_channel(
                self,
                guild_id=config.guild_id,
                channel_id=channel_id,
                channel_name=channel_name,
                discord_module=discord,
                error_label=_checkpoint_channel_error_label(config),
            )
            resolved_channel_id = getattr(channel, "id", None) or channel_id or 0
            plan_date = scan_time.date()
            async with self._checkpoint_lock():
                due_checkpoints = await asyncio.to_thread(
                    _resolve_due_checkpoints,
                    last_scan,
                    scan_time,
                )
                unsent_checkpoints = await asyncio.to_thread(
                    _filter_unsent_checkpoints,
                    resolved_channel_id,
                    due_checkpoints,
                )
                actionable_checkpoints = await asyncio.to_thread(
                    filter_unresponded_checkpoints,
                    plan_date,
                    unsent_checkpoints,
                )
            if not actionable_checkpoints:
                return

            include_response_prompt = config.notify_user_id is not None
            content = format_checkpoints_message(
                actionable_checkpoints,
                reference_time=scan_time,
                mention_user_id=config.notify_user_id,
                include_response_prompt=include_response_prompt,
            )
            await _send_channel_message_chunks(
                channel,
                content,
                allowed_mentions=_build_allowed_mentions(discord),
            )
            async with self._checkpoint_lock():
                await asyncio.to_thread(
                    _mark_checkpoints_sent,
                    resolved_channel_id,
                    actionable_checkpoints,
                )
                if include_response_prompt:
                    await asyncio.to_thread(
                        save_checkpoint_pending_response,
                        user_id=config.notify_user_id,
                        plan_date=plan_date,
                        channel_id=resolved_channel_id,
                        checkpoints=list(actionable_checkpoints),
                        sent_at=scan_time,
                    )

        def _checkpoint_lock(self) -> asyncio.Lock:
            if self._checkpoint_storage_lock is None:
                self._checkpoint_storage_lock = asyncio.Lock()
            return self._checkpoint_storage_lock

    bot = YuleDiscordBot()
    try:
        bot.run(config.token)
    except discord.LoginFailure as exc:
        raise ValueError(
            "Discord bot token login failed. Check DISCORD_BOT_TOKEN in .env.local and regenerate the token if needed."
        ) from exc
    except discord.NotFound as exc:
        error_code = getattr(exc, "code", None)
        if error_code == 10002:
            raise ValueError(
                "Discord application could not be found while syncing slash commands. "
                "Remove DISCORD_APPLICATION_ID or update it to match the bot token's application."
            ) from exc
        if error_code == 10004:
            raise ValueError(
                "Discord guild could not be found. Check DISCORD_GUILD_ID and make sure the bot was invited to that server."
            ) from exc
        raise


def _next_daily_run(target_time: time | None) -> datetime:
    if target_time is None:
        raise ValueError("daily briefing time is required for scheduling.")
    now = datetime.now().astimezone()
    next_run = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return next_run


def _collect_due_daily_preparation_steps(
    *,
    last_scan: datetime,
    scan_time: datetime,
    day_profile: DayProfile,
    completed_steps: set[tuple[str, str]],
) -> list[tuple[str, date, datetime]]:
    if scan_time <= last_scan:
        return []

    due_steps: list[tuple[str, date, datetime]] = []
    current_date = last_scan.date()
    end_date = scan_time.date()
    while current_date <= end_date:
        for step_name, scheduled_at in _daily_preparation_schedule_for(current_date, day_profile):
            step_key = (current_date.isoformat(), step_name)
            if step_key in completed_steps:
                continue
            if last_scan < scheduled_at <= scan_time:
                due_steps.append((step_name, current_date, scheduled_at))
        current_date = current_date + timedelta(days=1)

    due_steps.sort(key=lambda item: item[2])
    return due_steps


def _daily_preparation_schedule_for(plan_date: date, day_profile: DayProfile) -> list[tuple[str, datetime]]:
    morning_slot = next(slot for slot in day_profile.briefing_schedule(plan_date) if slot.briefing_type == "morning")
    briefing_at = morning_slot.send_at
    return [
        ("calendar_sync", briefing_at - timedelta(minutes=DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES)),
        ("github_sync", briefing_at - timedelta(minutes=DAILY_PREPARATION_GITHUB_OFFSET_MINUTES)),
        ("planning_snapshot", briefing_at - timedelta(minutes=DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES)),
    ]


def _cleanup_completed_preparation_steps(
    completed_steps: set[tuple[str, str]],
    *,
    today: date,
) -> None:
    stale_keys = [item for item in completed_steps if item[0] < today.isoformat()]
    for item in stale_keys:
        completed_steps.discard(item)


def _next_daily_preparation_runs(*, now: datetime, day_profile: DayProfile) -> tuple[datetime, datetime, datetime]:
    next_briefing = _next_scheduled_briefing_run(now=now, day_profile=day_profile, briefing_type="morning")

    return (
        next_briefing - timedelta(minutes=DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES),
        next_briefing - timedelta(minutes=DAILY_PREPARATION_GITHUB_OFFSET_MINUTES),
        next_briefing - timedelta(minutes=DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES),
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


def _next_checkpoint_scan(after: datetime | None = None) -> datetime:
    current = after or datetime.now().astimezone()
    rounded = current.replace(second=0, microsecond=0)
    if rounded <= current:
        rounded = rounded + timedelta(minutes=1)
    return rounded


def _checkpoint_channel_error_label(config: DiscordBotConfig) -> str:
    if config.checkpoint_channel_id is not None:
        return "DISCORD_CHECKPOINT_CHANNEL_ID"
    return "DISCORD_DAILY_CHANNEL_ID"


def _next_scheduled_briefing_run(
    *,
    now: datetime,
    day_profile: DayProfile,
    briefing_type: str | None,
) -> datetime:
    upcoming: list[datetime] = []
    for offset in range(0, 3):
        plan_date = now.date() + timedelta(days=offset)
        for slot in day_profile.briefing_schedule(plan_date):
            if briefing_type is not None and slot.briefing_type != briefing_type:
                continue
            if slot.send_at > now:
                upcoming.append(slot.send_at)
    if not upcoming:
        raise ValueError("no upcoming briefing schedule could be computed")
    return min(upcoming)


def _resolve_due_briefings(
    window_start: datetime,
    window_end: datetime,
) -> list[PlanningScheduledBriefing]:
    window_minutes = max(1, math.ceil((window_end - window_start).total_seconds() / 60))
    return build_due_briefings(window_start, window_minutes=window_minutes)


def _collect_due_briefing_slots(
    *,
    last_scan: datetime,
    scan_time: datetime,
    day_profile: DayProfile,
) -> list[tuple[DayProfileBriefingSlot, date]]:
    if scan_time <= last_scan:
        return []

    slots: list[tuple[DayProfileBriefingSlot, date]] = []
    plan_date = last_scan.date()
    end_date = scan_time.date()
    while plan_date <= end_date:
        for slot in day_profile.briefing_schedule(plan_date):
            if last_scan < slot.send_at <= scan_time:
                slots.append((slot, plan_date))
        plan_date += timedelta(days=1)
    slots.sort(key=lambda item: item[0].send_at)
    return slots


def _synthesize_scheduled_briefing(
    slot: DayProfileBriefingSlot,
    plan_date: date,
) -> PlanningScheduledBriefing:
    return PlanningScheduledBriefing(
        briefing_id=build_fallback_item_uid(
            "planning-scheduled-briefing", plan_date.isoformat(), slot.briefing_type
        ),
        briefing_type=slot.briefing_type,
        title=slot.title,
        send_at=slot.send_at.isoformat(),
        content="",
        source="rules",
    )


def _has_briefing_been_sent_async(channel_id: int | None, briefing_id: str) -> bool:
    if channel_id is None:
        return False
    entry = load_json_cache(
        namespace=BRIEFING_NOTIFICATION_NAMESPACE,
        cache_key=_briefing_notification_cache_key(channel_id, briefing_id),
        allow_stale=False,
        touch=False,
    )
    return entry is not None


def _briefing_notification_cache_key(channel_id: int, briefing_id: str) -> str:
    return f"{channel_id}:{briefing_id}"


def _filter_unsent_briefings(
    channel_id: int | None,
    briefings: list[PlanningScheduledBriefing],
) -> list[PlanningScheduledBriefing]:
    if channel_id is None:
        return briefings
    unsent: list[PlanningScheduledBriefing] = []
    for briefing in briefings:
        entry = load_json_cache(
            namespace=BRIEFING_NOTIFICATION_NAMESPACE,
            cache_key=_briefing_notification_cache_key(channel_id, briefing.briefing_id),
            allow_stale=False,
            touch=False,
        )
        if entry is None:
            unsent.append(briefing)
    return unsent


def _mark_briefings_sent(
    channel_id: int | None,
    briefings: list[PlanningScheduledBriefing],
) -> None:
    if channel_id is None:
        return
    for briefing in briefings:
        save_json_cache(
            namespace=BRIEFING_NOTIFICATION_NAMESPACE,
            cache_key=_briefing_notification_cache_key(channel_id, briefing.briefing_id),
            provider="discord-bot",
            range_start=briefing.send_at,
            range_end=briefing.send_at,
            scope_hash=str(channel_id),
            ttl_seconds=BRIEFING_NOTIFICATION_TTL_SECONDS,
            payload={
                "channel_id": channel_id,
                "briefing_id": briefing.briefing_id,
                "briefing_type": briefing.briefing_type,
                "send_at": briefing.send_at,
            },
            metadata={
                "channel_id": channel_id,
                "briefing_type": briefing.briefing_type,
            },
        )


def _save_discord_send_metric(
    *,
    workflow: str,
    started_at: datetime,
    duration_seconds: float,
    ok: bool,
    channel_id: int | None,
    message_count: int,
    snapshot_state: str,
    error: str | None = None,
) -> None:
    ended_at = datetime.now().astimezone()
    step = RuntimeStepMetric(
        name="discord_send",
        duration_seconds=duration_seconds,
        ok=ok,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        metadata={
            "channel_id": channel_id,
            "message_count": message_count,
            "snapshot_state": snapshot_state,
        },
        error=error,
    )
    save_runtime_metric_run(
        workflow=workflow,
        started_at=started_at,
        ended_at=ended_at,
        steps=[step],
        metadata={
            "channel_id": channel_id,
            "snapshot_state": snapshot_state,
        },
    )


def _snapshot_state_label(snapshot: object | None) -> str:
    if snapshot is None:
        return "missing"
    is_stale = getattr(snapshot, "is_stale", False)
    return "stale" if is_stale else "fresh"


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _resolve_messageable_channel(
    bot: "commands.Bot",
    *,
    guild_id: int,
    channel_id: int | None,
    channel_name: str | None,
    discord_module: "discord",
    error_label: str,
) -> "discord.abc.Messageable":
    channel = None
    fallback_used = False
    if channel_id is not None:
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                channel = None

    if channel is None and channel_name:
        if channel_id is not None:
            print(
                f"warning: {error_label} could not be resolved by id={channel_id}; "
                f"falling back to channel_name={channel_name!r}."
            )
        channel = await _find_messageable_channel_by_name(
            bot,
            guild_id=guild_id,
            channel_name=channel_name,
            discord_module=discord_module,
        )
        fallback_used = channel is not None

    if not isinstance(channel, discord_module.abc.Messageable):
        target_text = _channel_target_text(channel_id, channel_name)
        raise ValueError(f"Configured {error_label} could not be resolved to a messageable channel ({target_text}).")

    if fallback_used:
        print(
            f"info: resolved {error_label} by channel name fallback "
            f"({_channel_target_text(getattr(channel, 'id', None), channel_name)})"
        )

    return channel


async def _find_messageable_channel_by_name(
    bot: "commands.Bot",
    *,
    guild_id: int,
    channel_name: str,
    discord_module: "discord",
) -> "discord.abc.Messageable | None":
    normalized_target = _normalize_channel_name(channel_name)
    guild = bot.get_guild(guild_id)

    channels = []
    if guild is not None:
        channels.extend(getattr(guild, "channels", []) or [])
        fetch_channels = getattr(guild, "fetch_channels", None)
        if not channels and callable(fetch_channels):
            try:
                channels.extend(await fetch_channels())
            except Exception:
                pass

    if not channels:
        channels.extend(
            channel
            for channel in bot.get_all_channels()
            if getattr(getattr(channel, "guild", None), "id", None) == guild_id
        )

    for channel in channels:
        if _normalize_channel_name(getattr(channel, "name", None)) != normalized_target:
            continue
        if isinstance(channel, discord_module.abc.Messageable):
            return channel

    return None


async def _send_channel_message_chunks(
    channel: "discord.abc.Messageable",
    message: str,
    *,
    allowed_mentions: "discord.AllowedMentions",
) -> None:
    for chunk in split_discord_message(message):
        await channel.send(chunk, allowed_mentions=allowed_mentions)


def _should_handle_message(
    *,
    message: object,
    bot_user: object,
    conversation_channel_id: int | None,
    conversation_channel_name: str | None,
    conversation_reply_mode: str,
    daily_channel_id: int | None = None,
    daily_channel_name: str | None = None,
) -> bool:
    if conversation_reply_mode == "disabled":
        return False

    content = str(getattr(message, "content", "") or "").strip()
    if content.startswith("/"):
        return False

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    parent = getattr(channel, "parent", None)
    parent_id = getattr(parent, "id", None) or getattr(channel, "parent_id", None)
    channel_name = getattr(channel, "name", None)
    parent_name = getattr(parent, "name", None)

    daily_is_separate_channel = (
        daily_channel_id is not None
        and daily_channel_id != conversation_channel_id
    ) or (
        bool(_normalize_channel_name(daily_channel_name))
        and _normalize_channel_name(daily_channel_name)
        != _normalize_channel_name(conversation_channel_name)
    )
    if daily_is_separate_channel and _channel_matches_target(
        channel_id=channel_id,
        parent_id=parent_id,
        channel_name=channel_name,
        parent_name=parent_name,
        target_id=daily_channel_id,
        target_name=daily_channel_name,
    ):
        return False

    plain_message_allowed = conversation_reply_mode == "plain-message-or-mention"

    if plain_message_allowed and conversation_channel_id is not None and channel_id == conversation_channel_id:
        return True
    if plain_message_allowed and conversation_channel_id is not None and parent_id == conversation_channel_id:
        return True
    if plain_message_allowed and _normalize_channel_name(conversation_channel_name) and (
        _normalize_channel_name(channel_name) == _normalize_channel_name(conversation_channel_name)
        or _normalize_channel_name(parent_name) == _normalize_channel_name(conversation_channel_name)
    ):
        return True

    return _message_mentions_bot(message=message, bot_user=bot_user)


def _channel_matches_target(
    *,
    channel_id: int | None,
    parent_id: int | None,
    channel_name: str | None,
    parent_name: str | None,
    target_id: int | None,
    target_name: str | None,
) -> bool:
    if target_id is not None:
        if channel_id is not None and channel_id == target_id:
            return True
        if parent_id is not None and parent_id == target_id:
            return True
    target_name_normalized = _normalize_channel_name(target_name)
    if target_name_normalized:
        if _normalize_channel_name(channel_name) == target_name_normalized:
            return True
        if _normalize_channel_name(parent_name) == target_name_normalized:
            return True
    return False


def _extract_conversation_prompt(*, message: object, bot_user: object) -> str:
    content = str(getattr(message, "content", "") or "")
    bot_id = getattr(bot_user, "id", None)
    bot_name = str(getattr(bot_user, "name", "") or "").strip()

    if bot_id is not None:
        content = content.replace(f"<@{bot_id}>", " ")
        content = content.replace(f"<@!{bot_id}>", " ")
    if bot_name:
        content = content.replace(f"@{bot_name}", " ")

    return " ".join(content.split())


def _message_mentions_bot(*, message: object, bot_user: object) -> bool:
    mentions = getattr(message, "mentions", None) or []
    bot_id = getattr(bot_user, "id", None)
    return any(getattr(user, "id", None) == bot_id for user in mentions)


def _build_allowed_mentions(discord_module: "discord") -> "discord.AllowedMentions":
    return discord_module.AllowedMentions(
        users=True,
        roles=False,
        everyone=False,
        replied_user=False,
    )


def _make_engineering_send_chunks(discord_module: "discord"):
    allowed_mentions = _build_allowed_mentions(discord_module)

    async def _send(channel, text: str) -> None:
        if not text:
            return
        await _send_channel_message_chunks(
            channel,
            text,
            allowed_mentions=allowed_mentions,
        )

    return _send


_ENGINEERING_LAST_PROPOSED: dict[int, str] = {}
_ENGINEERING_LAST_RESEARCH_CONTEXT: dict[int, dict[str, Any]] = {}


def _remember_engineering_research_context(
    *,
    channel_id: int | None,
    intake_prompt: Any,
    research_pack: Any,
    collection_outcome: Any,
    role_for_research: str,
) -> None:
    if channel_id is None:
        return
    if research_pack is None and collection_outcome is None:
        return
    _ENGINEERING_LAST_RESEARCH_CONTEXT[channel_id] = {
        "intake_prompt": str(intake_prompt) if intake_prompt else None,
        "research_pack": research_pack,
        "collection_outcome": collection_outcome,
        "role_for_research": role_for_research,
    }


def _recall_engineering_research_context(
    *,
    channel_id: int | None,
    intake_prompt: Any,
    last_proposed: str | None,
) -> dict[str, Any]:
    if channel_id is None:
        return {}
    context = _ENGINEERING_LAST_RESEARCH_CONTEXT.get(channel_id) or {}
    if not context:
        return {}
    stored_prompt = context.get("intake_prompt")
    prompt_candidates = {
        value
        for value in (
            str(intake_prompt) if intake_prompt else None,
            last_proposed,
        )
        if value
    }
    if stored_prompt and prompt_candidates and stored_prompt not in prompt_candidates:
        return {}
    return context


def _default_engineering_conversation_fn(
    *,
    message_text: str,
    author_user_id: int | None,
    channel_id: int | None,
    bot_user: object,
    attachments: Sequence[Any] = (),
    user_links: Sequence[str] = (),
    auto_collect: bool = True,
    role_for_research: str = "engineering-agent/tech-lead",
    session_id: str | None = None,
):
    """Bridge to the engineering free-conversation layer.

    The conversation module is being landed in parallel; if it is not yet
    importable we degrade to a short fallback that points the user at the
    manual ``/engineer_intake`` slash command instead of crashing.
    """

    try:
        from . import engineering_conversation  # type: ignore
    except ImportError:
        return EngineeringConversationOutcome(
            content=(
                "엔지니어링 자유 대화 레이어가 아직 준비되지 않았습니다.\n"
                "지금은 `/engineer_intake` 슬래시 명령으로 작업을 등록해주세요."
            ),
        )

    builder = getattr(
        engineering_conversation,
        "build_engineering_conversation_response",
        None,
    )
    if builder is None:
        return EngineeringConversationOutcome(
            content=(
                "엔지니어링 대화 모듈이 응답 빌더를 노출하지 않았습니다.\n"
                "지금은 `/engineer_intake` 로 작업을 등록해주세요."
            ),
        )

    last_proposed = (
        _ENGINEERING_LAST_PROPOSED.get(channel_id) if channel_id is not None else None
    )

    def _load_latest_open_session_for_status(*, message_text: str | None = None) -> Any:
        """Resolve the session a status / diagnostic question targets.

        Lookup order — first hit wins:
          1. Explicit ``세션 <id>`` mention parsed out of ``message_text``.
          2. The session whose ``thread_id`` matches ``channel_id``
             (Discord exposes the thread id under ``channel.id`` when
             the user is sitting inside a work thread, so this hits
             for thread-bound status questions).
          3. The session whose ``extra['resumed_thread_id']`` matches
             ``channel_id`` (continuation may resume on a thread
             different from ``session.thread_id``).
          4. The latest open session for the channel/user pair (legacy
             behaviour kept as final fallback).

        Falls back to ``None`` on every lookup error so the
        conversation layer can render the no-session message.
        """

        # 1. Explicit session id in message body.
        if message_text:
            explicit_id = _extract_session_id_from_text(message_text)
            if explicit_id:
                try:
                    explicit_session = load_session(explicit_id)
                except Exception:  # noqa: BLE001
                    explicit_session = None
                if explicit_session is not None:
                    return explicit_session

        # 2. Thread anchor — when the user is asking from a work thread,
        # ``channel_id`` is actually the thread id. find_latest_open_session
        # returns None when no session has that thread_id (e.g. a
        # plain channel message), so this is safe to attempt always.
        if channel_id is not None:
            try:
                thread_match = find_latest_open_session(thread_id=channel_id)
            except Exception:  # noqa: BLE001
                thread_match = None
            if thread_match is not None:
                return thread_match

            # 3. Resumed thread id stashed on session.extra (continuation
            # may resume on a thread different from the original
            # session.thread_id; we can match either).
            try:
                resumed = _find_session_with_resumed_thread(channel_id)
            except Exception:  # noqa: BLE001
                resumed = None
            if resumed is not None:
                return resumed

        # 4. Channel + user fallback.
        try:
            return find_latest_open_session(
                channel_id=channel_id,
                user_id=author_user_id,
            )
        except Exception:  # noqa: BLE001 - best-effort lookup
            return None

    response = builder(
        message_text,
        author_user_id=author_user_id,
        mention_user=author_user_id is not None,
        last_proposed_prompt=last_proposed,
        auto_collect=auto_collect,
        user_links=tuple(user_links or ()),
        user_attachments=tuple(attachments or ()),
        role_for_research=role_for_research,
        session_id=session_id,
        status_session_loader=_load_latest_open_session_for_status,
    )

    intent_id = getattr(response, "intent_id", "")
    intake_prompt = getattr(response, "intake_prompt", None)
    ready_to_intake = bool(getattr(response, "ready_to_intake", False))
    research_pack = getattr(response, "research_pack", None)
    collection_outcome = getattr(response, "collection_outcome", None)
    response_role_for_research = str(
        getattr(response, "role_for_research", role_for_research)
        or role_for_research
    )
    if channel_id is not None:
        continuation_requested = bool(intake_prompt) and should_continue_existing_thread(
            message_text, str(intake_prompt)
        ) and not should_start_new_thread(message_text)
        if ready_to_intake and research_pack is None and collection_outcome is None:
            remembered_context = _recall_engineering_research_context(
                channel_id=channel_id,
                intake_prompt=intake_prompt,
                last_proposed=last_proposed,
            )
            if remembered_context:
                research_pack = remembered_context.get("research_pack")
                collection_outcome = remembered_context.get("collection_outcome")
                response_role_for_research = str(
                    remembered_context.get("role_for_research")
                    or response_role_for_research
                )
        if ready_to_intake and not continuation_requested:
            _ENGINEERING_LAST_PROPOSED.pop(channel_id, None)
            _ENGINEERING_LAST_RESEARCH_CONTEXT.pop(channel_id, None)
        elif intent_id in {
            "task_intake_candidate",
            "split_task_proposal",
            "needs_clarification",
        } and intake_prompt:
            _ENGINEERING_LAST_PROPOSED[channel_id] = str(intake_prompt)
            _remember_engineering_research_context(
                channel_id=channel_id,
                intake_prompt=intake_prompt,
                research_pack=research_pack,
                collection_outcome=collection_outcome,
                role_for_research=response_role_for_research,
            )

    return EngineeringConversationOutcome(
        content=str(getattr(response, "content", "") or ""),
        confirmed=ready_to_intake,
        intake_prompt=str(intake_prompt) if intake_prompt else None,
        write_requested=bool(getattr(response, "write_likely", False)),
        research_pack=research_pack,
        collection_outcome=collection_outcome,
        role_for_research=response_role_for_research,
        is_status_query=bool(getattr(response, "is_status_query", False)),
    )


def _default_engineering_intake_fn(
    *,
    prompt: str,
    write_requested: bool,
    channel_id: int | None,
    user_id: int | None,
):
    repo_root = Path(os.environ.get("YULE_REPO_ROOT", ".")).resolve()
    pool = build_participants_pool(repo_root, "engineering-agent")
    orchestrator = WorkflowOrchestrator(Dispatcher(pool))
    return orchestrator.intake(
        prompt=prompt,
        write_requested=write_requested,
        channel_id=channel_id,
        user_id=user_id,
    )


def _make_default_thread_continuation_fn(discord_module: "discord"):
    async def _continue(*, message, prompt, write_requested, thread_topic=None):
        channel = getattr(message, "channel", None)
        author = getattr(message, "author", None)
        user_id = getattr(author, "id", None)
        current_thread_id = _discord_thread_id(channel, discord_module)
        parent_channel_id = _discord_parent_channel_id(channel)
        channel_id = parent_channel_id or getattr(channel, "id", None)

        session = None
        if current_thread_id is not None:
            session = find_latest_open_session(
                thread_id=current_thread_id,
                user_id=user_id,
            )
        if session is None:
            session = find_latest_open_session(
                channel_id=channel_id,
                user_id=user_id,
            )
        if session is None and current_thread_id is not None:
            session = find_latest_open_session(thread_id=current_thread_id)
        if session is None or getattr(session, "thread_id", None) is None:
            return None

        thread_id = getattr(session, "thread_id", None)
        thread = await _resolve_thread_channel(
            thread_id=thread_id,
            fallback_channel=channel if current_thread_id == thread_id else None,
        )
        if thread is None:
            return None

        continuation_text = _format_engineering_continuation_message(
            session=session,
            prompt=prompt,
            write_requested=write_requested,
            topic=thread_topic,
        )
        for piece in chunk_for_discord_message(continuation_text) or (continuation_text,):
            await thread.send(piece)
        _clear_engineering_last_proposed_for_channel(message)

        # Persist the continuation prompt onto session.extra. The
        # session was created with a confirmation phrase like "새 작업으로
        # 진행" or "기존 세션으로 진행" as ``session.prompt`` — that
        # leaves the canonical task description blank, so a later
        # status / research / export turn looks at the wrong text. We
        # save the latest continuation under ``latest_continuation_prompt``
        # always, and also flip ``canonical_prompt_override`` to the
        # continuation prompt when the original was command-only.
        persisted = _record_engineering_continuation(
            session=session,
            continuation_prompt=prompt,
            resumed_thread_id=current_thread_id or thread_id,
        )
        if persisted is not None:
            session = persisted

        status = (
            "**[engineering-agent] 기존 thread에 이어서 접수**\n"
            f"세션 ID: `{session.session_id}`\n"
            f"thread id: `{thread_id}`\n"
            "새 작업 세션은 만들지 않았습니다."
        )
        return EngineeringThreadContinuation(
            session=session,
            thread_id=thread_id,
            message=status,
        )

    return _continue


# Phrases we treat as "no actual task description" — when
# ``session.prompt`` equals one of these we know the user said
# something like "이대로 진행" / "기존 세션으로 진행" without giving
# the gateway a real task description, so the continuation prompt
# should override it as the canonical record.
_CONTINUATION_COMMAND_ONLY_PROMPTS: tuple[str, ...] = (
    "새 작업으로 진행",
    "새 작업으로 시작",
    "이대로 진행",
    "이대로 등록",
    "그대로 진행",
    "그대로 등록",
    "기존 세션으로 진행",
    "기존 세션으로 시작",
    "기존 세션 진행",
    "기존 작업으로 진행",
    "기존 작업으로 시작",
    "기존 작업 진행",
    "이 thread로 진행",
    "이 thread에서 진행",
    "여기서 진행",
    "여기서 이어가",
    "확정",
    "진행",
    "ok",
)


_SESSION_ID_PATTERN = __import__("re").compile(
    r"(?:세션|session)\s*(?:id\s*[:=]?\s*)?[`'\"]?([0-9a-fA-F]{12})[`'\"]?",
    flags=__import__("re").IGNORECASE,
)


def _extract_session_id_from_text(text: str) -> str | None:
    """Pull a 12-hex session id out of a status / diagnostic question.

    The pattern is permissive about phrasing — we accept "세션
    abc123def456", "session abc123def456", and the same wrapped in
    backticks/quotes. A bare 12-hex token elsewhere in the message is
    NOT matched on purpose so a random hash in a URL doesn't hijack
    the lookup.
    """

    if not text:
        return None
    match = _SESSION_ID_PATTERN.search(text)
    if match is None:
        return None
    return match.group(1).lower()


def _find_session_with_resumed_thread(thread_id: int):
    """Return the open session whose ``extra['resumed_thread_id']``
    matches *thread_id*, or ``None``. Used as a fallback in the
    status-loader when ``session.thread_id`` is set to the original
    work thread but the user is asking from the resumed thread."""

    try:
        sessions = workflow_list_sessions(limit=50)
    except Exception:  # noqa: BLE001
        return None
    for session in sessions or ():
        state = getattr(session, "state", None)
        state_value = getattr(state, "value", state)
        if str(state_value).lower() in {"completed", "rejected"}:
            continue
        extra = dict(getattr(session, "extra", {}) or {})
        if extra.get("resumed_thread_id") == thread_id:
            return session
    return None


def _is_command_only_prompt(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalised = " ".join(value.lower().split())
    if not normalised:
        return True
    if len(normalised) <= 2:
        return True
    return normalised in _CONTINUATION_COMMAND_ONLY_PROMPTS


def _record_engineering_continuation(
    *,
    session,
    continuation_prompt: str,
    resumed_thread_id: int | None,
):
    """Append the continuation prompt to ``session.extra`` and persist.

    Returns the updated session (or the original when persistence isn't
    possible — production WorkflowSession is frozen, so we always
    return a replaced copy on success).
    """

    cleaned_prompt = (continuation_prompt or "").strip()
    if not cleaned_prompt:
        return session

    extra = dict(getattr(session, "extra", {}) or {})

    history = list(extra.get("continuation_requests") or ())
    history.append(
        {
            "prompt": cleaned_prompt,
            "thread_id": resumed_thread_id,
            "recorded_at": datetime.now().astimezone().isoformat(),
        }
    )
    # Cap the history so a single long-running session doesn't bloat
    # the SQLite cache row indefinitely.
    if len(history) > 20:
        history = history[-20:]
    extra["continuation_requests"] = history
    extra["latest_continuation_prompt"] = cleaned_prompt
    if resumed_thread_id is not None:
        extra["resumed_thread_id"] = resumed_thread_id
    if _is_command_only_prompt(getattr(session, "prompt", None)):
        # The original prompt was a command, not a task description —
        # let downstream readers prefer the continuation as the
        # canonical record.
        extra["canonical_prompt_override"] = cleaned_prompt

    try:
        from dataclasses import replace
        from ..agents.workflow_state import update_session

        updated = replace(session, extra=extra)
    except Exception:  # noqa: BLE001 - degrade gracefully for stub sessions
        live = getattr(session, "extra", None)
        if isinstance(live, dict):
            live.update(extra)
        return session
    try:
        update_session(updated, now=datetime.now().astimezone())
    except Exception:  # noqa: BLE001 - cache failure is non-fatal
        pass
    return updated


def _clear_engineering_last_proposed_for_channel(message) -> None:
    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    if channel_id is not None:
        try:
            normalized_channel_id = int(channel_id)
        except (TypeError, ValueError):
            return
        _ENGINEERING_LAST_PROPOSED.pop(normalized_channel_id, None)
        _ENGINEERING_LAST_RESEARCH_CONTEXT.pop(normalized_channel_id, None)


def _make_default_thread_kickoff_fn(discord_module: "discord"):
    async def _kickoff(*, channel, session, plan, topic):
        thread_topic = (topic or "").strip() or _default_engineering_thread_topic(session)

        thread_cls = getattr(discord_module, "Thread", None)
        if thread_cls is not None and isinstance(channel, thread_cls):
            thread_id = getattr(channel, "id", None)
            session_with_thread = _persist_engineering_thread_id(session, thread_id)
            kickoff_text = _format_engineering_kickoff_message(session_with_thread, plan)
            kickoff_with_directive = _append_team_kickoff_directive(
                kickoff_text, session_with_thread
            )
            for piece in chunk_for_discord_message(kickoff_with_directive) or (
                kickoff_with_directive,
            ):
                await channel.send(piece)
            return EngineeringThreadKickoff(
                thread_id=thread_id,
                message=kickoff_text,
            )

        thread = await _create_engineering_thread(
            channel=channel,
            name=thread_topic,
            discord_module=discord_module,
        )
        if thread is None:
            kickoff_text = _format_engineering_kickoff_message(session, plan)
            for piece in chunk_for_discord_message(kickoff_text) or (kickoff_text,):
                await channel.send(piece)
            return EngineeringThreadKickoff(thread_id=None, message=kickoff_text)

        thread_id = getattr(thread, "id", None)
        session_with_thread = _persist_engineering_thread_id(session, thread_id)
        kickoff_text = _format_engineering_kickoff_message(session_with_thread, plan)
        try:
            kickoff_with_directive = _append_team_kickoff_directive(
                kickoff_text, session_with_thread
            )
            for piece in chunk_for_discord_message(kickoff_with_directive) or (
                kickoff_with_directive,
            ):
                await thread.send(piece)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"warning: engineering thread kickoff send failed: {exc}")

        return EngineeringThreadKickoff(
            thread_id=thread_id,
            message=kickoff_text,
        )

    return _kickoff


def _persist_engineering_thread_id(session, thread_id):
    if session is None or thread_id is None:
        return session
    try:
        parsed_thread_id = int(thread_id)
    except (TypeError, ValueError):
        return session
    if getattr(session, "thread_id", None) == parsed_thread_id:
        return session
    try:
        updated = replace(session, thread_id=parsed_thread_id)
        return update_session(updated, now=datetime.now().astimezone())
    except Exception as exc:  # noqa: BLE001 - kickoff can still continue without persistence
        print(f"warning: engineering thread id persistence failed: {exc}")
        return session


def _discord_thread_id(channel, discord_module: "discord") -> int | None:
    thread_cls = getattr(discord_module, "Thread", None)
    if thread_cls is not None and isinstance(channel, thread_cls):
        try:
            return int(getattr(channel, "id", None))
        except (TypeError, ValueError):
            return None
    return None


def _discord_parent_channel_id(channel) -> int | None:
    parent = getattr(channel, "parent", None)
    raw = getattr(parent, "id", None) or getattr(channel, "parent_id", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _resolve_thread_channel(*, thread_id, fallback_channel=None):
    try:
        parsed_thread_id = int(thread_id)
    except (TypeError, ValueError):
        return None
    if getattr(fallback_channel, "id", None) == parsed_thread_id:
        return fallback_channel

    bot = _resolve_active_bot()
    if bot is not None:
        thread = bot.get_channel(parsed_thread_id)
        if thread is not None:
            return thread
        try:
            return await bot.fetch_channel(parsed_thread_id)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: engineering thread fetch failed: {exc}")
    return None


def _format_engineering_continuation_message(
    *,
    session,
    prompt: str,
    write_requested: bool,
    topic: str | None,
) -> str:
    lines = [
        "**[engineering-agent] 기존 작업 이어받음**",
        f"세션 ID: `{getattr(session, 'session_id', '?')}`",
    ]
    if topic:
        lines.append(f"주제: {topic}")
    lines.extend(
        [
            "",
            "**추가 요청**",
            _excerpt_text(prompt, limit=900),
        ]
    )
    if write_requested:
        lines.append("")
        lines.append("쓰기/수정 가능성이 있어 기존 승인 상태를 유지한 채로 검토를 이어갑니다.")
    lines.append("")
    lines.append("이 thread에서 자료 정리와 역할별 검토를 계속 진행합니다.")
    return "\n".join(lines)


def _append_team_kickoff_directive(message: str, session) -> str:
    if session is None:
        return message
    try:
        directive = kickoff_directive(session)
    except Exception as exc:  # noqa: BLE001 - keep kickoff visible even if team chain cannot start
        print(f"warning: engineering team kickoff directive failed: {exc}")
        return message
    return f"{message}\n\n{directive}"


async def _create_engineering_thread(
    *,
    channel,
    name: str,
    discord_module: "discord",
):
    create_thread = getattr(channel, "create_thread", None)
    if not callable(create_thread):
        return None

    channel_type = getattr(discord_module, "ChannelType", None)
    public_thread_type = getattr(channel_type, "public_thread", None) if channel_type else None
    auto_archive_minutes = 60 * 24

    try:
        if public_thread_type is not None:
            return await create_thread(
                name=name,
                type=public_thread_type,
                auto_archive_duration=auto_archive_minutes,
            )
        return await create_thread(name=name, auto_archive_duration=auto_archive_minutes)
    except TypeError:
        try:
            return await create_thread(name=name)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: engineering thread creation failed: {exc}")
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"warning: engineering thread creation failed: {exc}")
        return None


def _default_engineering_thread_topic(session) -> str:
    if session is None:
        return "engineering-agent 작업"
    session_id = getattr(session, "session_id", None) or "?"
    task_type = getattr(session, "task_type", None) or "task"
    return f"engineer-{task_type}-{session_id}"[:90]


def _excerpt_text(text: str, *, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned or "(요청 본문 없음)"
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


def _make_default_engineering_research_loop_fn(discord_module: "discord"):
    """Build the research_loop hook injected into the engineering router.

    Returned coroutine signature mirrors ``ResearchLoopFn`` from the
    router. On each call we:

      1. Run :func:`run_research_loop` (sync, no I/O).
      2. If insufficient, surface the Korean follow-up prompt and stop.
      3. Otherwise publish via :func:`publish_research_loop_to_forum`
         using a discord.py-backed thread/post pair.
    """

    forum_ctx = ResearchForumContext.from_env()

    async def _hook(*, session, message_text, attachments, channel, **kwargs):
        collection_outcome = kwargs.get("collection_outcome")
        incoming_pack = kwargs.get("research_pack") or getattr(
            collection_outcome, "pack", None
        )
        role_for_research = kwargs.get("role_for_research")
        forum_comment_mode = resolve_forum_comment_mode()

        try:
            outcome = await asyncio.to_thread(
                run_research_loop,
                session=session,
                message_text=message_text,
                attachments=tuple(attachments or ()),
                research_pack=incoming_pack,
                collection=collection_outcome,
            )
        except Exception as exc:  # noqa: BLE001 - non-fatal; reported by router
            return EngineeringResearchLoopReport(
                error=f"research loop 실패: {exc}",
            )

        if outcome.insufficient:
            return EngineeringResearchLoopReport(
                follow_up_message=outcome.follow_up_prompt
                or "자료가 부족합니다. 참고 링크나 이미지를 올려주세요.",
                insufficient=True,
            )

        persisted_session = _persist_research_pack_for_member_bots(
            outcome.session,
            outcome.research_pack,
            collection_outcome=collection_outcome,
            synthesis=getattr(outcome, "synthesis", None),
            synthesis_text=getattr(outcome, "synthesis_text", None),
        )
        if persisted_session is not outcome.session:
            outcome = replace(outcome, session=persisted_session)

        if not forum_ctx.configured:
            # Forum disabled — still tell the operator the deliberation
            # ran so they can pull `outcome.assignments` from logs/CLI.
            return EngineeringResearchLoopReport(
                forum_status_message=_format_research_forum_disabled_status(outcome),
            )

        try:
            publish = await publish_research_loop_to_forum(
                outcome,
                forum_context=forum_ctx,
                create_thread_fn=_make_default_research_forum_create_thread_fn(discord_module),
                post_message_fn=_make_default_research_forum_post_message_fn(discord_module),
                posted_by="bot:engineering-agent",
                collection_outcome=collection_outcome,
                collection_role=role_for_research,
                collection_next_steps=(
                    "tech-lead가 문제 정의와 조사 범위를 먼저 정리합니다.",
                    "각 역할 봇이 forum thread에서 순서대로 자기 관점의 검토를 남깁니다.",
                    "마지막 tech-lead가 합의안과 다음 행동을 종합합니다.",
                ),
                comment_mode=forum_comment_mode,
            )
        except Exception as exc:  # noqa: BLE001 - reported through router
            return EngineeringResearchLoopReport(
                error=f"forum publish 실패: {exc}",
            )

        # Persist forum publication mode + kickoff outcome onto the
        # session so the status / diagnostic responder can describe the
        # live setup ("member-bots 모드, open-call 게시 완료") without
        # having to reach back into the publish object. Best-effort —
        # the report itself is what the user sees right now, so a
        # cache write failure must not crash the hook.
        try:
            _persist_forum_comment_mode_to_session(
                session=outcome.session,
                publish=publish,
            )
        except Exception:  # noqa: BLE001 - cache failure is non-fatal
            pass

        return _research_loop_report_from_publish(outcome, publish)

    return _hook


def _persist_forum_comment_mode_to_session(*, session, publish) -> None:
    """Merge member-bots / gateway mode signals into ``session.extra``.

    Called after every successful forum publish so subsequent status
    diagnostic answers reflect the actual mode that ran. Idempotent —
    if the same session is republished the keys are simply overwritten.
    """

    kickoff = getattr(publish, "kickoff_comment", None)
    is_member_bots = kickoff is not None
    extra_updates = {
        "forum_comment_mode": "member-bots" if is_member_bots else "gateway",
    }
    if is_member_bots:
        extra_updates["forum_kickoff_posted"] = bool(getattr(kickoff, "posted", False))
        kickoff_error = getattr(kickoff, "error", None)
        if kickoff_error is not None:
            extra_updates["forum_kickoff_error"] = str(kickoff_error)
        else:
            # Drop a stale error from a previous failure so the next
            # diagnostic doesn't surface it after a retry succeeded.
            extra_updates["forum_kickoff_error"] = None

    merged_extra = {**dict(getattr(session, "extra", {}) or {}), **extra_updates}
    updated = replace(session, extra=merged_extra)
    update_session(updated, now=datetime.now().astimezone())


def _persist_research_pack_for_member_bots(
    session,
    pack,
    *,
    collection_outcome=None,
    synthesis=None,
    synthesis_text=None,
):
    """Thin wrapper around :func:`persist_research_artifacts`.

    Kept as a stable name for the forum research-loop hook + tests that
    already mock this symbol. The router persists the same pack earlier
    (right after intake) — this call additionally lands synthesis and
    collection metadata once deliberation finishes. ``persist_research_artifacts``
    is idempotent so the double-write is safe.
    """

    return persist_research_artifacts(
        session,
        pack,
        collection_outcome=collection_outcome,
        synthesis=synthesis,
        synthesis_text=synthesis_text,
    )


def _format_research_forum_disabled_status(outcome) -> str:
    """Status line we show when ResearchForumContext is unconfigured.

    The deliberation already ran, so the operator gets the synthesis +
    assignment count without forum publication.
    """

    assignments = list(outcome.assignments)
    parts = [
        "ℹ️ 운영-리서치 forum env 미설정 — deliberation 결과는 로컬에 보존됩니다.",
        f"역할 배정 {len(assignments)}건"
        + (f" · 실행 후보 `{outcome.session.executor_role}`" if outcome.session.executor_role else ""),
    ]
    hints = _format_research_hints_for_outcome(outcome)
    if hints:
        parts.append(hints)
    return "\n".join(parts)


def _format_research_hints_for_outcome(outcome) -> str:
    """Format per-role research hints derived from research_profiles.

    Glue between :mod:`agents.research.profiles` and the live engineering
    research loop output. When the loop already knows the session role
    sequence and task_type, we can show the operator which source types,
    queries, and reference categories each role should pull next. Empty
    string is returned when no role yields hints (unknown roles, no
    sequence, ...) so callers can append it unconditionally.
    """

    session = getattr(outcome, "session", None)
    if session is None:
        return ""
    role_sequence = tuple(getattr(session, "role_sequence", ()) or ())
    task_type = getattr(session, "task_type", None)
    return format_research_hints_block(role_sequence, task_type)


def _research_loop_report_from_publish(
    outcome, publish
) -> EngineeringResearchLoopReport:
    if publish.skipped_reason:
        return EngineeringResearchLoopReport(
            forum_status_message=f"ℹ️ forum 게시 생략 — {publish.skipped_reason}",
        )

    thread = publish.thread
    if thread is None:
        return EngineeringResearchLoopReport(
            error="forum thread 생성 결과가 비어 있습니다.",
        )

    if not thread.posted:
        # The fallback markdown can run thousands of chars. The status
        # message gets sent to a regular Discord channel (2000-char hard
        # cap, split into chunks), so cap the embedded fallback at the
        # forum starter limit and append the truncation notice. Operators
        # can recover the full record from the Obsidian export.
        raw_fallback = thread.fallback_markdown or thread.error or "forum 게시 실패"
        fallback_text = truncate_for_starter_message(
            raw_fallback,
            limit=FORUM_STARTER_CONTENT_LIMIT,
        )
        return EngineeringResearchLoopReport(
            forum_status_message=f"⚠️ 운영-리서치 forum 게시 실패 — fallback markdown:\n{fallback_text}",
            error=thread.error,
        )

    # member-bots mode signal: the publisher only sets
    # ``kickoff_comment`` when it tried to post the open-call directive
    # that the per-role member bots react to. Gateway mode never sets
    # it. Use that to switch summary wording — gateway-mode "역할별
    # 댓글 N건" is misleading in member-bots mode where the gateway
    # never posts those comments by design.
    kickoff_comment = getattr(publish, "kickoff_comment", None)
    is_member_bots_mode = kickoff_comment is not None
    kickoff_posted_flag: Optional[bool] = (
        bool(getattr(kickoff_comment, "posted", False))
        if kickoff_comment is not None
        else None
    )
    kickoff_error_text: Optional[str] = (
        _optional_str_value(getattr(kickoff_comment, "error", None))
        if kickoff_comment is not None
        else None
    )

    lines: list[str] = ["✅ 운영-리서치 forum 게시 완료"]
    if thread.thread_url:
        lines.append(f"thread: {thread.thread_url}")
    elif thread.thread_id:
        lines.append(f"thread id: {thread.thread_id}")

    if is_member_bots_mode:
        lines.append("모드: member-bots (각 멤버 봇이 자기 계정으로 댓글)")
        if kickoff_posted_flag:
            lines.append("open-call directive: 게시 완료")
        else:
            reason = kickoff_error_text or "원인 미확인"
            lines.append(f"open-call directive: 게시 실패 — {reason}")
        lines.append(
            "각 멤버 봇의 후속 댓글은 운영-리서치 thread에서 확인하세요."
        )
    else:
        role_count = len(publish.role_comments)
        decision_ok = bool(publish.decision_comment and publish.decision_comment.posted)
        lines.append(
            f"역할별 댓글 {role_count}건 · tech-lead 종합 {'기록' if decision_ok else '미기록'}"
        )

    if outcome.assignments:
        executor = next((a for a in outcome.assignments if a.is_executor), None)
        if executor:
            lines.append(
                f"실행 후보 `{executor.role}` 작업 {len(executor.actions)}건 배정 완료"
            )

    hints = _format_research_hints_for_outcome(outcome)
    if hints:
        lines.append(hints)

    return EngineeringResearchLoopReport(
        forum_status_message="\n".join(lines),
        forum_thread_id=thread.thread_id,
        forum_thread_url=thread.thread_url,
        forum_comment_mode="member-bots" if is_member_bots_mode else "gateway",
        kickoff_posted=kickoff_posted_flag,
        kickoff_error=kickoff_error_text,
    )


def _optional_str_value(value: Any) -> Optional[str]:
    """Tiny helper for normalising error strings — kept out of the
    `_research_loop_report_from_publish` body so the top-level reads as
    ``mode → wording → return``."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _make_default_research_forum_create_thread_fn(discord_module: "discord"):
    """Wrap discord.py forum-channel thread creation for research_forum."""

    async def _create(*, channel_id, channel_name, name, content, **_):
        bot = _resolve_active_bot()
        if bot is None:
            raise RuntimeError("discord bot client not ready")
        channel = await _resolve_research_forum_channel(
            bot=bot,
            channel_id=channel_id,
            channel_name=channel_name,
            discord_module=discord_module,
        )
        if channel is None:
            raise RuntimeError(
                f"research forum channel not found (id={channel_id}, name={channel_name})"
            )
        create_thread = getattr(channel, "create_thread", None)
        if not callable(create_thread):
            raise RuntimeError("research forum channel does not support create_thread")
        thread_result = await create_thread(name=name, content=content)
        # discord.py returns ``ThreadWithMessage`` for forums; pull the thread.
        thread = getattr(thread_result, "thread", thread_result)
        return {
            "id": getattr(thread, "id", None),
            "url": getattr(thread, "jump_url", None) or getattr(thread, "url", None),
        }

    return _create


def _make_default_research_forum_post_message_fn(discord_module: "discord"):
    """Wrap discord.py thread-message send for research_forum comments."""

    async def _post(*, thread_id, content, **_):
        bot = _resolve_active_bot()
        if bot is None:
            raise RuntimeError("discord bot client not ready")
        thread = bot.get_channel(int(thread_id))
        if thread is None:
            try:
                thread = await bot.fetch_channel(int(thread_id))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"thread {thread_id} not reachable: {exc}") from exc
        # Defence in depth: callers in research_forum.py / research_loop.py
        # already chunk before they reach this wrapper, but a stray caller
        # (or a re-issued legacy code path) could still pass a > 1900 char
        # string. Run it through the chunker once more so Discord never
        # sees a content above the cap. ``id`` is the first chunk's id so
        # downstream persistence keeps a stable handle.
        pieces = chunk_for_discord_message(content) or (content,)
        first_message_id = None
        for piece in pieces:
            sent = await thread.send(piece)
            if first_message_id is None:
                first_message_id = getattr(sent, "id", None)
        return {"id": first_message_id}

    return _post


_ACTIVE_DISCORD_BOT: Any = None


def _set_active_discord_bot(bot: Any) -> None:
    """Register the running bot so research_forum wrappers can find it.

    ``run_discord_bot`` is the only caller that constructs a real bot,
    and it does so once per process; we keep the slot at module level so
    the deeply-nested research_loop hooks can fetch it without weaving the
    bot reference through every closure.
    """

    global _ACTIVE_DISCORD_BOT
    _ACTIVE_DISCORD_BOT = bot


def _resolve_active_bot() -> Any:
    return _ACTIVE_DISCORD_BOT


async def _resolve_research_forum_channel(
    *,
    bot: Any,
    channel_id: Optional[int],
    channel_name: Optional[str],
    discord_module: "discord",
):
    """Find the configured forum channel by id, then by name fallback."""

    if channel_id is not None:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(channel_id))
            except Exception:  # noqa: BLE001
                channel = None
        if channel is not None:
            return channel
    if channel_name:
        target = str(channel_name).strip().lstrip("#").lower()
        for guild in bot.guilds:
            for ch in getattr(guild, "channels", []):
                if str(getattr(ch, "name", "")).strip().lower() == target:
                    return ch
    return None


def _format_engineering_kickoff_message(session, plan) -> str:
    lines: list[str] = ["**[engineering-agent] 작업 thread 시작**"]
    if session is not None:
        session_id = getattr(session, "session_id", None)
        if session_id:
            lines.append(f"세션 ID: `{session_id}`")
        task_type = getattr(session, "task_type", None)
        if task_type:
            lines.append(f"분류: {task_type}")
        executor_role = getattr(session, "executor_role", None)
        executor_runner = getattr(session, "executor_runner", None)
        if executor_role:
            lines.append(f"실행 후보: {executor_role} ({executor_runner or '?'})")
    if plan is not None:
        role_sequence = getattr(plan, "role_sequence", None)
        if role_sequence:
            lines.append(f"참여 후보: {', '.join(role_sequence)}")
    lines.append("")
    lines.append("이 thread에서 각 멤버 봇의 조사, 실행 메모, 결과 회신을 이어 갑니다.")
    return "\n".join(lines)


def _checkpoint_window_minutes(window_start: datetime, window_end: datetime) -> int:
    total_seconds = max(0.0, (window_end - window_start).total_seconds())
    return max(1, math.ceil(total_seconds / 60.0))


def _resolve_due_checkpoints(window_start: datetime, window_end: datetime) -> list[PlanningCheckpoint]:
    prefetched_checkpoints, cache_complete = load_prefetched_due_checkpoints(window_start, window_end)
    if cache_complete:
        return prefetched_checkpoints

    return build_due_checkpoints(
        window_start,
        window_minutes=_checkpoint_window_minutes(window_start, window_end),
    )


def _filter_unsent_checkpoints(
    channel_id: int,
    checkpoints: list[PlanningCheckpoint],
) -> list[PlanningCheckpoint]:
    return [
        checkpoint
        for checkpoint in checkpoints
        if not _has_checkpoint_been_sent(channel_id, checkpoint.checkpoint_id)
    ]


def _mark_checkpoints_sent(channel_id: int, checkpoints: list[PlanningCheckpoint]) -> None:
    for checkpoint in checkpoints:
        save_json_cache(
            namespace=CHECKPOINT_NOTIFICATION_NAMESPACE,
            cache_key=_checkpoint_cache_key(channel_id, checkpoint.checkpoint_id),
            provider="discord-bot",
            range_start=None,
            range_end=None,
            scope_hash=str(channel_id),
            ttl_seconds=CHECKPOINT_NOTIFICATION_TTL_SECONDS,
            payload={
                "channel_id": channel_id,
                "checkpoint_id": checkpoint.checkpoint_id,
                "remind_at": checkpoint.remind_at,
            },
            metadata={"kind": checkpoint.kind},
        )


def _has_checkpoint_been_sent(channel_id: int, checkpoint_id: str) -> bool:
    entry = load_json_cache(
        namespace=CHECKPOINT_NOTIFICATION_NAMESPACE,
        cache_key=_checkpoint_cache_key(channel_id, checkpoint_id),
        touch=False,
    )
    return entry is not None


def _checkpoint_cache_key(channel_id: int, checkpoint_id: str) -> str:
    return f"{channel_id}:{checkpoint_id}"


def _normalize_channel_name(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("#").lower()


def _channel_target_text(channel_id: int | None, channel_name: str | None) -> str:
    parts = []
    if channel_id is not None:
        parts.append(f"channel_id={channel_id}")
    if channel_name:
        parts.append(f"channel_name={channel_name}")
    return ", ".join(parts) if parts else "channel=unconfigured"


def _cleanup_preparation_context(
    context_store: dict[str, dict[str, object]],
    *,
    today: date,
) -> None:
    stale_keys = [key for key in context_store if key < today.isoformat()]
    for key in stale_keys:
        context_store.pop(key, None)


def _preparation_source_label(source_statuses, source_type: str) -> str:
    for status in source_statuses:
        if getattr(status, "source_type", None) == source_type:
            return str(getattr(status, "source_id", "unknown"))
    return "unknown"


def _log_preparation_event(
    *,
    level: str,
    event: str,
    step_name: str,
    plan_date: str,
    scheduled_at: str,
    ok: bool | None = None,
    attempt: int | None = None,
    attempt_limit: int | None = None,
    duration_seconds: float | None = None,
    retry_scheduled: bool | None = None,
    retry_delay_seconds: int | None = None,
    metadata: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "component": "discord-daily-preparation",
        "event": event,
        "step_name": step_name,
        "plan_date": plan_date,
        "scheduled_at": scheduled_at,
    }
    if ok is not None:
        payload["ok"] = ok
    if attempt is not None:
        payload["attempt"] = attempt
    if attempt_limit is not None:
        payload["attempt_limit"] = attempt_limit
    if duration_seconds is not None:
        payload["duration_seconds"] = round(duration_seconds, 3)
    if retry_scheduled is not None:
        payload["retry_scheduled"] = retry_scheduled
    if retry_delay_seconds is not None:
        payload["retry_delay_seconds"] = retry_delay_seconds
    if metadata:
        payload["metadata"] = metadata
    if error:
        payload["error"] = error
    print(f"{level}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}")


def _save_preparation_metric(
    *,
    step_name: str,
    plan_date: str,
    started_at: datetime,
    duration_seconds: float,
    ok: bool,
    metadata: dict[str, object],
    error: str | None = None,
) -> None:
    ended_at = datetime.now().astimezone()
    step = RuntimeStepMetric(
        name=step_name,
        duration_seconds=duration_seconds,
        ok=ok,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        metadata=metadata,
        error=error,
    )
    save_runtime_metric_run(
        workflow="discord-daily-preparation",
        started_at=started_at,
        ended_at=ended_at,
        steps=[step],
        metadata={
            "plan_date": plan_date,
            "step_name": step_name,
        },
    )
