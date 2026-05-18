from __future__ import annotations

import asyncio
import json
import math
import os
import time as time_module
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ...agents import (
    Dispatcher,
    WorkflowOrchestrator,
    build_participants_pool,
)
from ...agents.workflow_state import (
    find_latest_open_session,
    list_sessions as workflow_list_sessions,
    load_session,
    update_session,
)
from ...integrations.calendar import list_naver_calendar_items
from ...integrations.calendar.models import build_fallback_item_uid
from ...integrations.github.issues import list_open_issues
from ...integrations.github.pulls import list_open_pull_requests
from ...observability import RuntimeStepMetric, save_runtime_metric_run
from ...planning import build_daily_plan, collect_planning_inputs, load_reminder_items, save_daily_plan_snapshot
from ...planning.day_profile import DayProfile, DayProfileBriefingSlot, load_day_profile
from ...planning.models import PlanningCheckpoint, PlanningScheduledBriefing
from ...storage import load_json_cache, save_json_cache
from ..runtime.checkpoint_state import (
    filter_unresponded_checkpoints,
    save_checkpoint_pending_response,
)
from ..commands import register_discord_commands, resolve_bot_role_set_from_env
from ..conversation import build_conversation_response_envelope
from ..config import DiscordBotConfig
from ..engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadKickoff,
    EngineeringThreadContinuation,
    route_engineering_message,
    should_continue_existing_thread,
    should_start_new_thread,
)
from ..research_forum import (
    FORUM_STARTER_CONTENT_LIMIT,
    ResearchForumContext,
    chunk_for_discord_message,
    truncate_for_starter_message,
)
from ..ui.typing_indicator import (
    typing_context,
    typing_keepalive,
    wrap_send_chunks_with_typing,
)
from ...agents.research.loop import (
    publish_research_loop_to_forum,
    run_research_loop,
)
from ...agents.research.collector import resolve_forum_comment_mode
from ...agents.deliberation import synthesis_to_dict
from ...agents.research.pack import pack_to_dict
from ...agents.research.persistence import persist_research_artifacts
from ...agents.research.profiles import format_research_hints_block
from ..engineering_team_runtime import kickoff_directive
from ..ui.formatter import (
    format_checkpoints_message,
    format_plan_today_message,
    format_scheduled_briefing_message,
    format_snapshot_regenerating_message,
    format_snapshot_regeneration_failed_message,
    split_discord_message,
)
from ..runtime.planning import build_due_checkpoints, load_plan_today_snapshot
from ..runtime.planning import build_due_briefings, load_prefetched_due_checkpoints, prefetch_checkpoint_snapshots
from ..runtime.snapshot_refresh import regenerate_today_snapshot
from .channels import _channel_target_text, _normalize_channel_name
from .startup import (
    _channel_configuration_warnings,
    _channel_overlap_warnings,
    _startup_messages,
)
from .scheduling import (
    BRIEFING_NOTIFICATION_NAMESPACE,
    BRIEFING_NOTIFICATION_TTL_SECONDS,
    CHECKPOINT_NOTIFICATION_NAMESPACE,
    CHECKPOINT_NOTIFICATION_TTL_SECONDS,
    DAILY_PREPARATION_CALENDAR_OFFSET_MINUTES,
    DAILY_PREPARATION_GITHUB_OFFSET_MINUTES,
    DAILY_PREPARATION_SNAPSHOT_OFFSET_MINUTES,
    _briefing_notification_cache_key,
    _checkpoint_cache_key,
    _checkpoint_channel_error_label,
    _checkpoint_window_minutes,
    _cleanup_completed_preparation_steps,
    _collect_due_briefing_slots,
    _collect_due_daily_preparation_steps,
    _daily_preparation_schedule_for,
    _filter_unsent_briefings,
    _filter_unsent_checkpoints,
    _has_briefing_been_sent_async,
    _has_checkpoint_been_sent,
    _mark_briefings_sent,
    _mark_checkpoints_sent,
    _next_checkpoint_scan,
    _next_daily_preparation_runs,
    _next_daily_run,
    _next_scheduled_briefing_run,
    _resolve_due_briefings,
    _resolve_due_checkpoints,
    _save_discord_send_metric,
    _synthesize_scheduled_briefing,
)

DAILY_PREPARATION_GITHUB_LIMIT = 30


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
            # Role-scoped registration: planning-bot subprocess registers
            # only planning commands, engineering gateway only ``/engineer_*``.
            # The follow-up ``tree.sync`` PUTs the resulting set on Discord
            # so any stale commands from a previous boot (e.g. ``/engineer_*``
            # left over on planning-bot) get cleared.
            bot_role_set = resolve_bot_role_set_from_env()
            register_discord_commands(
                self,
                guild_id=config.guild_id,
                notify_user_id=config.notify_user_id,
                role_set=bot_role_set,
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

            # M6.1b-2: route #승인-대기 replies through the queue
            # (handle_approval_reply) before the engineering route.
            # Approval replies live in their own channel — they're not
            # engineering intake — so the router short-circuits when
            # it handles a message. The legacy in-channel obsidian
            # approval phrase flow on the work thread still runs
            # because that's a different channel.
            approval_route_result = await _route_engineering_approval_reply(
                message=message,
                bot_user=self.user,
                discord_module=discord,
            )
            if approval_route_result is not None and approval_route_result.handled:
                return

            # A-M7.5b: forum-thread message routing — Obsidian save
            # request → #승인-대기 producer, role-change request →
            # active_research_roles update. Lazy adapter; pays no
            # cost when the message is not in a forum thread.
            forum_route_result = await _route_forum_thread_message(
                message=message,
                content_text=content_text,
                discord_module=discord,
            )
            if forum_route_result is not None and forum_route_result.handled:
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


async def run_engineering_gateway_until_shutdown(
    *,
    shutdown_event: asyncio.Event,
    bot_factory: Any,
    token: str,
) -> None:
    """SIGTERM-aware gateway runner — A-M6.2.

    Replaces the M6.1b-2 ``asyncio.to_thread(run_discord_bot, ...)``
    path used by ``yule run-service eng-discord-gateway``. The
    legacy thread path relied on discord.py installing its own
    signal handlers, which only works when ``bot.run`` owns the
    main thread. Under ``run-service`` the runtime owns the main
    loop, so signals went to the runtime's handler instead and the
    gateway never saw them.

    This helper drives the bot through the awaitable
    ``bot.start(token)`` and races it against *shutdown_event*. On
    SIGTERM the runtime sets the event and we call
    ``await bot.close()`` so the gateway disconnects gracefully
    instead of the parent killing it mid-WebSocket.

    *bot_factory* is a zero-arg callable that returns a discord.py
    ``Client``-shaped object. Production wires it to a closure that
    calls :func:`build_engineering_gateway_bot`; tests pass a fake
    client so the shutdown race can be exercised without discord.py.

    Returns when the bot exits cleanly or shutdown is observed.
    Login failures raise the same way ``run_discord_bot`` does.
    """

    import discord

    bot = bot_factory()

    async def _waiter() -> None:
        await shutdown_event.wait()
        try:
            await bot.close()
        except Exception:  # noqa: BLE001 - graceful close best-effort
            pass

    waiter_task = asyncio.create_task(_waiter())
    try:
        await bot.start(token)
    except discord.LoginFailure as exc:
        raise ValueError(
            "Discord bot token login failed. Check DISCORD_BOT_TOKEN in .env.local and regenerate the token if needed."
        ) from exc
    finally:
        waiter_task.cancel()
        try:
            await waiter_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def build_engineering_gateway_bot(repo_root: Path) -> Any:
    """Construct (without running) the engineering gateway bot.

    Used by :func:`run_engineering_gateway_until_shutdown` to get
    the same :class:`YuleDiscordBot` instance ``run_discord_bot``
    builds. ``run_discord_bot`` defines its bot class inside its
    function body, so we monkeypatch ``commands.Bot.run`` for one
    call to capture the constructed instance — the patch is
    guarded by ``try/finally`` so it can't leak.

    A future refactor that lifts ``YuleDiscordBot`` to module scope
    will let us drop this introspection trick. Today the function
    body is ~750 lines of closure-captured config, so the refactor
    is bigger than this milestone.

    A-M11b: after the bot is captured we install the engineering
    role-runner dispatcher from env. The install is best-effort —
    if the env names no provider (or every provider is unavailable)
    the dispatcher stays at the deterministic terminal and the
    in-process role bodies behave exactly as before. Failure during
    install is logged via the trace return and never raised so the
    gateway always boots.
    """

    from discord.ext import commands

    captured: dict[str, Any] = {}
    sentinel: Any = type("_BotConstructed", (BaseException,), {})

    original_run = commands.Bot.run

    def _capture_run(self, *_args, **_kwargs):
        captured["bot"] = self
        raise sentinel()

    commands.Bot.run = _capture_run  # type: ignore[assignment]
    try:
        try:
            run_discord_bot(repo_root)
        except sentinel:  # type: ignore[misc]
            pass
    finally:
        commands.Bot.run = original_run  # type: ignore[assignment]

    bot = captured.get("bot")
    if bot is None:
        raise RuntimeError(
            "build_engineering_gateway_bot: run_discord_bot did not construct a bot"
        )

    _install_engineering_role_runner_dispatch_for_gateway()
    return bot


def _install_engineering_role_runner_dispatch_for_gateway() -> None:
    """Best-effort role-runner wiring for the engineering gateway.

    Call site for both :func:`build_engineering_gateway_bot` (run-service
    entrypoint) and :func:`_run_discord_gateway` in
    :mod:`runtime.run_service`. Idempotent — calling twice just rebinds
    the dispatcher to the latest env snapshot.

    Failure here MUST NOT propagate: a missing or partially configured
    runner backend is recoverable (the deterministic in-process body
    keeps the gateway useful), so we log and move on.
    """

    try:
        from ...agents.runners.bootstrap import (
            install_engineering_role_runner_dispatch,
        )
    except Exception as exc:  # noqa: BLE001 - partial install fallback
        print(
            f"warning: role-runner bootstrap import failed ({type(exc).__name__}); "
            "gateway continues with deterministic in-process role bodies"
        )
        return

    def _on_failure(exc: BaseException) -> None:
        # Sanitised — never log the env value or stack frames containing
        # secrets. Only the exception type is surfaced.
        print(
            "warning: role-runner dispatch install failed "
            f"({type(exc).__name__}); using deterministic fallback"
        )

    try:
        trace = install_engineering_role_runner_dispatch(
            on_install_failure=_on_failure
        )
    except Exception as exc:  # noqa: BLE001 - never let bootstrap kill the gateway
        _on_failure(exc)
        return
    if trace is None:
        # Engineering runtime module wasn't importable; the in-process
        # body remains. install_engineering_role_runner_dispatch already
        # logged a warning.
        return

    # Friendly status line for run-service stdout — operator sees which
    # providers are configured/available without grepping logs.
    available = [
        e.provider for e in trace.entries if e.configured and e.available
    ]
    if trace.deterministic_fallback_only:
        print(
            "role-runner dispatch installed: deterministic fallback only "
            f"(opted-in providers: {[e.provider for e in trace.entries if e.configured] or 'none'})"
        )
    else:
        print(
            f"role-runner dispatch installed: priority={available} + deterministic terminal"
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


async def _route_forum_thread_message(
    *,
    message: Any,
    content_text: str,
    discord_module: "discord",
):
    """Adapter from ``on_message`` into the M7.5 forum-thread helpers.

    Returns ``None`` (treated as fall-through) when the message
    isn't from a forum thread or carries no recognisable intent.
    A non-``None`` :class:`ForumMessageRouteResult` whose
    ``handled`` is True means the user got a friendly reply
    (Obsidian save approval card created, role-change applied,
    or a context-missing notice) and ``on_message`` must short-
    circuit the engineering route.

    Lazy import + no-cost fall-through: a message in a regular
    text channel never reaches the SQLite open / approval-worker
    construction path because the adapter exits at the
    ``parent_id`` check before touching any of that.
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return None
    # Cheap exit when not a thread — avoids the lazy-import cost
    # for every regular-channel message.
    if (
        getattr(channel, "parent_id", None) is None
        and getattr(channel, "parent", None) is None
    ):
        return None

    from ..forum.message_adapter import route_forum_message

    return await route_forum_message(
        message=message,
        text=content_text,
        discord_module=discord_module,
        send_chunks_factory=_make_engineering_send_chunks,
    )


async def _route_engineering_approval_reply(
    *,
    message: Any,
    bot_user: Any,
    discord_module: "discord",
):
    """Adapter from ``on_message`` into the queue-side approval
    reply router (M5a-2 + M6.1b-2).

    Returns ``None`` when env isn't configured for the approval
    channel — the caller falls through to the engineering route.
    Otherwise returns the helper's
    :class:`ApprovalReplyRouteResult` so ``on_message`` can decide
    whether to short-circuit.

    This adapter takes the worker construction cost (queue +
    obsidian writer) only when the message lands in the approval
    channel; for unrelated messages we exit fast on the channel
    matcher.
    """

    import os
    from ..approval.reply_router import (
        is_approval_channel_message,
        route_approval_channel_message,
    )

    raw_id = (os.environ.get("DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID") or "").strip()
    approval_channel_id: int | None
    try:
        approval_channel_id = int(raw_id) if raw_id else None
    except ValueError:
        approval_channel_id = None
    approval_channel_name = (
        os.environ.get("DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME") or ""
    ).strip() or None

    if approval_channel_id is None and not approval_channel_name:
        # Env unset — gateway is running without an approval channel.
        # Skip the matcher entirely so we don't pay the import /
        # SQLite open cost for unrelated messages.
        return None

    if not is_approval_channel_message(
        message=message,
        approval_channel_id=approval_channel_id,
        approval_channel_name=approval_channel_name,
    ):
        return None

    from ...agents.job_queue import (
        HeartbeatStore,
        JobQueue,
        ObsidianWriterWorker,
        default_render_fn,
        default_vault_root_resolver,
        default_write_fn,
    )
    from ...agents.workflow_state import list_sessions as _list_sessions

    queue = JobQueue()
    obsidian_worker = ObsidianWriterWorker(
        queue=queue,
        heartbeats=HeartbeatStore(),
        render_fn=default_render_fn,
        write_fn=default_write_fn,
        vault_root_resolver=default_vault_root_resolver,
    )
    send_chunks = _make_engineering_send_chunks(discord_module)

    def _session_lister():
        try:
            return _list_sessions()
        except Exception:  # noqa: BLE001 - best-effort lookup
            return ()

    # P1-L-3 — production wiring: live PRMergeExecutor + on_result 콜백.
    # env (YULE_GITHUB_APP_MERGE_OPT_IN + GITHUB_APP_*) 가 갖춰지면 실제
    # merge 호출, 아니면 None — handle_pr_merge_approval_reply 가
    # ``merge_disabled`` 결과를 반환해 RESPONSE_PR_MERGE_DISABLED 로 ack.
    pr_merge_executor = _build_pr_merge_executor_for_bot()

    def _on_pr_merge_result(result):
        """approval reply 가 merge 성공한 직후 호출되는 후크.

        merge_sha 가 있으면 ``pr_merged`` stage 로 advance + next slice
        dispatch. background continuation loop 가 이미 같은 작업을
        한다 — 두 경로 모두 idempotent (advance_stage / next_slice
        dispatcher 가 pr_merge_stage 가드).
        """

        try:
            _advance_to_merged_and_dispatch_next_slice(result=result)
        except Exception:  # noqa: BLE001 - never crash reply router
            pass

    return await route_approval_channel_message(
        message=message,
        bot_user=bot_user,
        queue=queue,
        obsidian_worker=obsidian_worker,
        approval_channel_id=approval_channel_id,
        approval_channel_name=approval_channel_name,
        session_lister=_session_lister,
        send_chunks=send_chunks,
        pr_merge_executor=pr_merge_executor,
        on_pr_merge_result=_on_pr_merge_result,
        pr_merge_ready_for_review_action=_build_ready_for_review_action_for_bot(),
    )


def _build_ready_for_review_action_for_bot():
    """env 가 갖춰지면 live ``mark_pull_request_ready_for_review`` callable
    반환.  없으면 None — 그 경우 draft escalation 승인은 ``draft_ready_
    for_review_failed`` 로 reject + audit 에 사유 명시.

    P1-Q D — draft escalation reply path 전용.  merge executor 와 동일
    env contract (``YULE_GITHUB_APP_MERGE_OPT_IN`` + GitHub App config)
    재사용.
    """

    try:
        from ...runtime.coding_executor_runner import (
            ENV_GITHUB_APP_MERGE_OPT_IN,
            _opt_in_enabled,
        )
        from ...github_app.live_client import build_live_client_from_env
    except Exception:  # noqa: BLE001
        return None
    if not _opt_in_enabled():
        return None
    try:
        live = build_live_client_from_env()
    except Exception:  # noqa: BLE001
        return None

    def _action(*, repo: str, pr_number: int, **_kwargs):
        return live.mark_pull_request_ready_for_review(
            repo=repo, pr_number=int(pr_number)
        )

    return _action


def _build_pr_merge_executor_for_bot():
    """env 가 갖춰지면 live :data:`PRMergeExecutor` 반환. 없으면 None.

    `runtime.coding_executor_runner._maybe_build_live_pr_merge_executor`
    와 정확히 동일한 환경 contract — 두 군데 동시 wiring 보장.
    """

    try:
        from ...runtime.coding_executor_runner import (
            _maybe_build_live_pr_merge_executor,
        )
    except Exception:  # noqa: BLE001
        return None
    try:
        return _maybe_build_live_pr_merge_executor()
    except Exception:  # noqa: BLE001
        return None


def _advance_to_merged_and_dispatch_next_slice(*, result) -> None:
    """reply router 가 merge 성공 직후 호출.

    1. result.proposal 에서 session_id 추출 (proposal.extra 에 stamp)
    2. session.extra 를 ``pr_merged`` stage 로 advance + audit
    3. dispatch_next_coding_slice 호출 (backlog 있으면 다음 slice 자동
       enqueue, 비어있으면 session done)
    """

    proposal = getattr(result, "proposal", None)
    merge_result = getattr(result, "merge_result", None) or {}
    merge_sha = str(merge_result.get("merge_sha") or "")
    if proposal is None or not merge_sha:
        return
    extra = dict(proposal.extra or {})
    session_id = str(extra.get("session_id") or "")
    if not session_id:
        return

    from ...agents.job_queue.next_slice_dispatcher import (
        dispatch_next_coding_slice,
    )
    from ...agents.job_queue.pr_merge_continuation import (
        STAGE_PR_MERGED,
        advance_stage,
    )
    from ...agents.workflow_state import load_session
    from ...runtime.coding_executor_runner import (
        _build_next_slice_dispatcher,
        _persist_session_extra,
    )

    session = load_session(session_id)
    if session is None:
        return
    current_extra = getattr(session, "extra", None) or {}
    if not isinstance(current_extra, dict):
        current_extra = dict(current_extra) if current_extra else {}
    # 같은 sha 로 이미 pr_merged 면 no-op — 사람 reply + bg loop 가
    # 동시에 들어와도 중복 advance 방지.
    from ...agents.job_queue.pr_merge_continuation import EXTRA_PR_MERGE_STAGE

    if current_extra.get(EXTRA_PR_MERGE_STAGE) == STAGE_PR_MERGED:
        return
    new_extra = advance_stage(
        current_extra,
        new_stage=STAGE_PR_MERGED,
        reason="approval_reply_merged",
        merge_sha=merge_sha,
        method=str(merge_result.get("method") or "squash"),
    )
    _persist_session_extra(session_id, new_extra)

    enqueue_slice, on_done = _build_next_slice_dispatcher()

    def _persist(updated: Mapping[str, Any], _sid=session_id) -> None:
        _persist_session_extra(_sid, updated)

    fresh = load_session(session_id)
    fresh_extra = getattr(fresh, "extra", None) or {} if fresh else {}
    dispatch_next_coding_slice(
        session_id=session_id,
        session_extra=fresh_extra,
        persist_extra=_persist,
        enqueue_slice=enqueue_slice,
        on_session_done=on_done,
    )


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


_NL_HELP_TRIGGERS: tuple[str, ...] = (
    "help",
    "/help",
    "도움말",
    "도움",
    "사용법",
    "헬프",
    "어떻게 써",
    "어떻게 쓰",
    "어떻게 사용",
    "뭐 할 수 있",
    "뭘 할 수 있",
    "기능 알려",
    "what can you do",
    "engineer_help",
    "/engineer_help",
)


def _looks_like_nl_help(message_text: str) -> bool:
    """True when *message_text* is a plain-language help ask.

    Mirrors the slash command (``/help`` / ``/engineer_help``) and the
    engineering_conversation ``GENERAL_ENGINEERING_HELP`` intent — kept
    here as a tiny standalone matcher so the import-fallback path can
    still answer help questions even if the conversation module did
    not load.
    """

    if not message_text:
        return False
    normalized = " ".join(message_text.lower().split())
    if not normalized:
        return False
    short_tokens = {"help", "도움말", "도움", "사용법", "헬프", "h", "?"}
    if normalized in short_tokens:
        return True
    return any(trigger in normalized for trigger in _NL_HELP_TRIGGERS)


def _build_help_or_intake_fallback(*, reason: str) -> "EngineeringConversationOutcome":
    """Reply when the conversation module isn't available.

    Historically this surface was a hard "지금은 /engineer_intake 슬래시
    명령으로 작업을 등록해주세요" line — which forced new users to learn
    a slash command before they could talk to the bot at all. We now
    surface the canonical help body (same as ``/help``) so onboarding
    still works, and frame ``/engineer_intake`` as one of several
    options rather than the only path.
    """

    from ..engineering.help_surface import render_engineer_help_short

    body = (
        f"⚠️ {reason} — 정식 자유 대화 흐름은 잠깐 막혀 있어요.\n"
        "그래도 사용법 안내와 명령 흐름은 그대로 동작합니다:\n\n"
        f"{render_engineer_help_short()}\n\n"
        "지금 바로 실행 요청을 등록하시려면 `/engineer_intake <요청 내용>` 으로 시작할 수 있고, "
        "단순 질문이나 상담은 잠시 후 다시 자연어로 말씀하셔도 됩니다."
    )
    return EngineeringConversationOutcome(content=body)


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

    The conversation module is normally always importable in production;
    when it isn't (test harness, partial install, in-flight refactor)
    we still want the user to be able to *talk* to the bot without
    bouncing off a hard "use the slash command" wall.  The fallback
    surface therefore (1) answers the natural-language help intent
    inline so onboarding still works, and (2) explains the intake
    escalation path as an option, not a requirement.
    """

    try:
        from .. import engineering_conversation  # type: ignore
    except ImportError:
        return _build_help_or_intake_fallback(
            reason="자유 대화 레이어가 아직 준비되지 않았습니다",
        )

    builder = getattr(
        engineering_conversation,
        "build_engineering_conversation_response",
        None,
    )
    if builder is None:
        return _build_help_or_intake_fallback(
            reason="대화 모듈이 응답 빌더를 아직 노출하지 않았습니다",
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

    # P0-K (#148) — never let a command-only operational phrase
    # ("진행 해줘" / "이대로 진행" / "작업 승인 할게 진행 해줘") become
    # latest_continuation_prompt or canonical_prompt_override. The
    # research loop + forum thread title reader treat those keys as
    # *task* prompts; persisting the operational phrase causes
    # "[Reference] 진행 해줘" thread spam.
    try:
        from ...agents.routing import is_non_actionable_prompt
    except Exception:  # noqa: BLE001 - partial install safe-side
        is_non_actionable_prompt = None  # type: ignore[assignment]
    prompt_is_command_only = bool(
        is_non_actionable_prompt is not None
        and is_non_actionable_prompt(cleaned_prompt)
    )

    extra = dict(getattr(session, "extra", {}) or {})

    history = list(extra.get("continuation_requests") or ())
    history.append(
        {
            "prompt": cleaned_prompt,
            "thread_id": resumed_thread_id,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "is_command_only": prompt_is_command_only,
        }
    )
    # Cap the history so a single long-running session doesn't bloat
    # the SQLite cache row indefinitely.
    if len(history) > 20:
        history = history[-20:]
    extra["continuation_requests"] = history
    if resumed_thread_id is not None:
        extra["resumed_thread_id"] = resumed_thread_id

    if prompt_is_command_only:
        # P0-K — record the *event* (history above) so audit + status
        # can show the user pressed approval/proceed, but do NOT
        # overwrite latest_continuation_prompt / canonical_prompt_override
        # with the command-only phrase. Existing values stay intact.
        extra.setdefault("command_only_continuation_count", 0)
        extra["command_only_continuation_count"] = (
            extra["command_only_continuation_count"] + 1
        )
    else:
        extra["latest_continuation_prompt"] = cleaned_prompt
        if _is_command_only_prompt(getattr(session, "prompt", None)):
            # The original prompt was a command, not a task description —
            # let downstream readers prefer the continuation as the
            # canonical record.
            extra["canonical_prompt_override"] = cleaned_prompt

    try:
        from dataclasses import replace
        from ...agents.workflow_state import update_session

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
    # A-M7.5b — append the tech-lead routing summary right after the
    # plan so the operator sees who is participating, who is on
    # standby, and how to extend the team in the same kickoff post.
    summary = _build_kickoff_routing_summary(session)
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")
    lines.append("이 thread에서 각 멤버 봇의 조사, 실행 메모, 결과 회신을 이어 갑니다.")
    return "\n".join(lines)


def _build_kickoff_routing_summary(session: Any) -> Optional[str]:
    """Render the M7.5 routing summary for the kickoff message.

    Returns ``None`` when the session has no role-selection metadata
    so legacy kickoff messages stay byte-identical (existing
    ``_format_engineering_kickoff_message`` callers keep their
    rendered text).
    """

    if session is None:
        return None
    extra = dict(getattr(session, "extra", None) or {})
    selected = extra.get("active_research_roles")
    if not isinstance(selected, (list, tuple)) or not selected:
        return None
    try:
        from ...agents.lifecycle.role_selection import (
            ROLE_TECH_LEAD,
            RoleSelection,
            SOURCE_USER_ALL_TEAM,
            format_routing_summary,
        )
    except Exception:  # noqa: BLE001 - role_selection import failure → no summary
        return None

    excluded = extra.get("excluded_research_roles") or []
    primary = extra.get("role_selection_primary") or []
    reviewer = extra.get("role_selection_reviewer") or []
    optional = extra.get("role_selection_optional") or []
    reasons = extra.get("role_selection_reasons") or {}
    source = extra.get("role_selection_source") or "fallback"
    fallback_policy = extra.get("role_selection_fallback_policy")
    participation = extra.get("role_participation") or {}

    selection = RoleSelection(
        selected_roles=tuple(selected),
        excluded_roles=tuple(excluded),
        required_roles=(ROLE_TECH_LEAD,),
        optional_roles=tuple(optional),
        reason_by_role=dict(reasons) if isinstance(reasons, dict) else {},
        selection_source=str(source),
        participation_by_role=dict(participation)
        if isinstance(participation, dict)
        else {},
        primary_roles=tuple(primary),
        reviewer_roles=tuple(reviewer),
        optional_roles_v2=tuple(optional),
        matched_keywords_by_role={},
        fallback_policy=str(fallback_policy) if fallback_policy else None,
    )
    label = (extra.get("role_selection_request_label") or "").strip() or None
    return format_routing_summary(selection, request_label=label)


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
