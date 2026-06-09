from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from yule_orchestrator.agents import (
    Dispatcher,
    TaskType,
    WorkflowError,
    WorkflowOrchestrator,
    build_participants_pool,
)
from yule_orchestrator.agents.review_loop import (
    ReviewFeedback,
    ReviewSeverity,
    ReviewSource,
)
from ..engineering.help_surface import render_engineer_help_message
from ..ui.formatter import (
    format_checkpoints_message,
    format_plan_today_message,
    format_snapshot_regenerating_message,
    format_snapshot_regeneration_failed_message,
    split_discord_message,
)
from ..runtime.planning import build_due_checkpoints, load_plan_today_snapshot


class BotRoleSet(str, Enum):
    """Which slash-command group a bot process should register.

    Owning the right set per process prevents the wrong app from
    receiving a command (e.g. planning-bot getting ``/engineer_intake``
    and timing out because it has no orchestrator wiring).
    """

    ALL = "all"
    PLANNING_ONLY = "planning-only"
    ENGINEERING_ONLY = "engineering-only"


DISCORD_BOT_ROLE_ENV = "DISCORD_BOT_ROLE"

_ROLE_FROM_ENV: dict[str, BotRoleSet] = {
    "planning": BotRoleSet.PLANNING_ONLY,
    "planning-only": BotRoleSet.PLANNING_ONLY,
    "planning-bot": BotRoleSet.PLANNING_ONLY,
    "engineering-gateway": BotRoleSet.ENGINEERING_ONLY,
    "engineering-only": BotRoleSet.ENGINEERING_ONLY,
    "engineering": BotRoleSet.ENGINEERING_ONLY,
    "all": BotRoleSet.ALL,
    "": BotRoleSet.ALL,
}


def resolve_bot_role_set_from_env(env: Optional[Mapping[str, str]] = None) -> BotRoleSet:
    """Map ``DISCORD_BOT_ROLE`` env value to a :class:`BotRoleSet`.

    Unset / unknown values fall back to :attr:`BotRoleSet.ALL` so existing
    single-process callers keep their previous behavior.
    """

    source = env if env is not None else os.environ
    raw = (source.get(DISCORD_BOT_ROLE_ENV) or "").strip().lower()
    return _ROLE_FROM_ENV.get(raw, BotRoleSet.ALL)


def register_discord_commands(
    bot: "commands.Bot",
    guild_id: int,
    notify_user_id: int | None = None,
    *,
    role_set: BotRoleSet = BotRoleSet.ALL,
) -> None:
    """Register slash commands on *bot*'s tree.

    *role_set* controls which command groups are attached. The default
    keeps the historic behavior (every command on every bot); production
    callers now pass a narrower set so the wrong application can never
    own a command. Planning-only / engineering-only convenience wrappers
    (:func:`register_planning_commands`, :func:`register_engineering_commands`)
    front this so call sites stay readable.
    """

    import discord
    from discord import app_commands

    _bind_discord_runtime_globals(discord_module=discord, app_commands_module=app_commands)
    guild = discord.Object(id=guild_id)
    allowed_mentions = _build_allowed_mentions(discord)

    if role_set in (BotRoleSet.ALL, BotRoleSet.PLANNING_ONLY):
        _register_planning_commands_impl(
            bot,
            guild=guild,
            allowed_mentions=allowed_mentions,
            notify_user_id=notify_user_id,
            discord=discord,
            app_commands=app_commands,
        )
    if role_set in (BotRoleSet.ALL, BotRoleSet.ENGINEERING_ONLY):
        _register_engineering_commands_impl(
            bot,
            guild=guild,
            allowed_mentions=allowed_mentions,
            discord=discord,
            app_commands=app_commands,
        )


def register_planning_commands(
    bot: "commands.Bot",
    guild_id: int,
    notify_user_id: int | None = None,
) -> None:
    """Register only the planning-bot owned commands (ping / plan_today / checkpoints_now)."""

    register_discord_commands(
        bot,
        guild_id,
        notify_user_id,
        role_set=BotRoleSet.PLANNING_ONLY,
    )


def register_engineering_commands(
    bot: "commands.Bot",
    guild_id: int,
    notify_user_id: int | None = None,
) -> None:
    """Register only the engineering-gateway owned ``/engineer_*`` commands."""

    register_discord_commands(
        bot,
        guild_id,
        notify_user_id,
        role_set=BotRoleSet.ENGINEERING_ONLY,
    )


def _register_planning_commands_impl(
    bot: "commands.Bot",
    *,
    guild: Any,
    allowed_mentions: Any,
    notify_user_id: int | None,
    discord: Any,
    app_commands: Any,
) -> None:

    @bot.tree.command(name="ping", description="봇이 살아 있는지 확인합니다.", guild=guild)
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("pong")

    @bot.tree.command(name="plan_today", description="저장된 오늘 daily-plan snapshot을 보여줍니다.", guild=guild)
    async def plan_today(interaction: discord.Interaction) -> None:
        if not await _safe_defer(interaction, discord_module=discord):
            return
        plan_date = date.today()
        recipient_mention = notify_user_id or interaction.user.id
        snapshot = await asyncio.to_thread(load_plan_today_snapshot, plan_date)

        if snapshot is None:
            ack = format_snapshot_regenerating_message(
                mention_user_id=recipient_mention,
                slot_title="오늘 브리핑",
            )
            await _send_message_chunks(
                interaction,
                ack,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
            ensure_snapshot = getattr(bot, "ensure_snapshot", None)
            if ensure_snapshot is None:
                fail = format_snapshot_regeneration_failed_message(
                    mention_user_id=recipient_mention,
                    error="snapshot 자동 재생성 기능을 찾지 못했습니다.",
                )
                await _send_message_chunks(
                    interaction,
                    fail,
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            snapshot, error = await ensure_snapshot(plan_date)
            if snapshot is None:
                fail = format_snapshot_regeneration_failed_message(
                    mention_user_id=recipient_mention,
                    error=error,
                )
                await _send_message_chunks(
                    interaction,
                    fail,
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return

        content = format_plan_today_message(
            snapshot.envelope,
            mention_user_id=recipient_mention,
            snapshot=snapshot,
        )
        await _send_message_chunks(
            interaction,
            content,
            allowed_mentions=allowed_mentions,
            discord_module=discord,
        )

    @bot.tree.command(name="checkpoints_now", description="지금 기준으로 다가오는 체크포인트를 보여줍니다.", guild=guild)
    @app_commands.describe(window_minutes="몇 분 앞까지 확인할지 설정합니다.")
    async def checkpoints_now(
        interaction: discord.Interaction,
        window_minutes: app_commands.Range[int, 1, 60] = 10,
    ) -> None:
        if not await _safe_defer(interaction, discord_module=discord):
            return
        now = datetime.now().astimezone()
        due_checkpoints = await asyncio.to_thread(
            build_due_checkpoints,
            now,
            window_minutes=window_minutes,
        )
        content = format_checkpoints_message(
            due_checkpoints,
            reference_time=now,
            mention_user_id=notify_user_id or interaction.user.id,
        )
        await _send_message_chunks(
            interaction,
            content,
            allowed_mentions=allowed_mentions,
            discord_module=discord,
        )

    # engineer_* commands are registered by _register_engineering_commands_impl
    # so the planning-bot application never owns them. See BotRoleSet docs.
    return


def _register_engineering_commands_impl(
    bot: "commands.Bot",
    *,
    guild: Any,
    allowed_mentions: Any,
    discord: Any,
    app_commands: Any,
) -> None:
    async def _send_help_response(interaction: "discord.Interaction") -> None:
        if not await _safe_defer(interaction, discord_module=discord):
            return
        await _send_message_chunks(
            interaction,
            render_engineer_help_message(),
            allowed_mentions=allowed_mentions,
            discord_module=discord,
        )

    @bot.tree.command(
        name="help",
        description="engineering-agent 봇 사용법 (자유 대화 vs intake, 주요 명령, 예시).",
        guild=guild,
    )
    async def help_command(interaction: "discord.Interaction") -> None:
        try:
            await _send_help_response(interaction)
        except Exception as exc:  # noqa: BLE001
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="help",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_help",
        description="`/help` 와 동일 — 명령 이름 충돌 시 fallback 으로 호출하세요.",
        guild=guild,
    )
    async def engineer_help_command(interaction: "discord.Interaction") -> None:
        try:
            await _send_help_response(interaction)
        except Exception as exc:  # noqa: BLE001
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_help",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_intake",
        description="engineering-agent에게 작업을 위임합니다 (접수 메시지를 채널에 게시).",
        guild=guild,
    )
    @app_commands.describe(
        prompt="자연어 작업 요청.",
        task_type="명시 task type (생략 시 키워드 분류).",
        write_requested="이 작업이 코드/문서 쓰기를 요구하는지 여부.",
    )
    async def engineer_intake(
        interaction: "discord.Interaction",
        prompt: str,
        task_type: Optional[str] = None,
        write_requested: bool = False,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                result = await asyncio.to_thread(
                    _run_engineer_intake,
                    prompt=prompt,
                    task_type=task_type,
                    write_requested=write_requested,
                    channel_id=interaction.channel_id,
                    user_id=interaction.user.id,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer intake 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                result.message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_intake",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_show",
        description="engineering-agent 워크플로 세션 상태를 조회합니다.",
        guild=guild,
    )
    @app_commands.describe(session_id="조회할 워크플로 세션 id.")
    async def engineer_show(
        interaction: "discord.Interaction",
        session_id: str,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                session = await asyncio.to_thread(_load_engineer_session, session_id=session_id)
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer show 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            if session is None:
                await _send_message_chunks(
                    interaction,
                    f"session `{session_id}` 을 찾을 수 없습니다.",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            summary = (
                f"**[engineering-agent] 세션 상태**\n"
                f"세션 ID: `{session.session_id}`\n"
                f"상태: {session.state.value}\n"
                f"분류: {session.task_type}\n"
                f"실행 후보: {session.executor_role} ({session.executor_runner or '?'})"
            )
            if session.write_blocked_reason:
                summary += f"\n승인 대기: {session.write_blocked_reason}"
            await _send_message_chunks(
                interaction,
                summary,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_show",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_review",
        description="기존 세션에 PR 리뷰/Copilot/외부 피드백을 입력합니다.",
        guild=guild,
    )
    @app_commands.describe(
        session_id="피드백을 연결할 워크플로 세션 ID.",
        summary="한 줄 요약 (라우팅에 사용).",
        body="피드백 본문 (선택).",
        severity="blocking / high / medium / low / nit (기본: medium).",
        categories="쉼표로 구분한 카테고리 라벨 (예: ui, copy).",
        source="github_pr_review / github_copilot / external_agent / user (기본: user).",
        file_paths="쉼표로 구분한 영향 파일 경로 (선택).",
    )
    async def engineer_review(
        interaction: "discord.Interaction",
        session_id: str,
        summary: str,
        body: Optional[str] = None,
        severity: Optional[str] = None,
        categories: Optional[str] = None,
        source: Optional[str] = None,
        file_paths: Optional[str] = None,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                result = await asyncio.to_thread(
                    _run_engineer_review,
                    session_id=session_id,
                    summary=summary,
                    body=body,
                    severity=severity,
                    categories=categories,
                    source=source,
                    file_paths=file_paths,
                    channel_id=interaction.channel_id,
                    thread_id=getattr(interaction.channel, "id", None),
                    user_id=interaction.user.id,
                    author_name=str(interaction.user),
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer review 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                result.message + f"\n\n_피드백 ID_: `{result.feedback.feedback_id}`",
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_review",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_review_reply",
        description="리뷰 피드백에 적용/제안/남은 이슈 회신을 게시합니다.",
        guild=guild,
    )
    @app_commands.describe(
        session_id="회신 대상 워크플로 세션 ID.",
        feedback_id="회신 대상 feedback ID.",
        applied="적용한 수정 (개행 또는 ; 으로 분리).",
        proposed="추가 제안 (선택, 개행 또는 ; 분리).",
        remaining="남은 이슈 (선택, 개행 또는 ; 분리).",
    )
    async def engineer_review_reply(
        interaction: "discord.Interaction",
        session_id: str,
        feedback_id: str,
        applied: str,
        proposed: Optional[str] = None,
        remaining: Optional[str] = None,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                result = await asyncio.to_thread(
                    _run_engineer_review_reply,
                    session_id=session_id,
                    feedback_id=feedback_id,
                    applied=applied,
                    proposed=proposed,
                    remaining=remaining,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer review reply 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                result.message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_review_reply",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_approve",
        description="engineering-agent 세션을 승인해 진행을 풀어줍니다.",
        guild=guild,
    )
    @app_commands.describe(session_id="승인할 워크플로 세션 ID.")
    async def engineer_approve(
        interaction: "discord.Interaction",
        session_id: str,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                message = await asyncio.to_thread(
                    _run_engineer_approve,
                    session_id=session_id,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer approve 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_approve",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_reject",
        description="engineering-agent 세션을 거절합니다 (사유 필수).",
        guild=guild,
    )
    @app_commands.describe(
        session_id="거절할 워크플로 세션 ID.",
        reason="거절 사유 (운영 기록용, 한 줄).",
    )
    async def engineer_reject(
        interaction: "discord.Interaction",
        session_id: str,
        reason: str,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                message = await asyncio.to_thread(
                    _run_engineer_reject,
                    session_id=session_id,
                    reason=reason,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer reject 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_reject",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_progress",
        description="진행 중인 engineering-agent 세션에 진행 메모를 남깁니다.",
        guild=guild,
    )
    @app_commands.describe(
        session_id="메모를 남길 워크플로 세션 ID.",
        note="진행 메모 (한 줄, PR/Thread 링크는 본문에 그대로 붙여도 됩니다).",
    )
    async def engineer_progress(
        interaction: "discord.Interaction",
        session_id: str,
        note: str,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                message = await asyncio.to_thread(
                    _run_engineer_progress,
                    session_id=session_id,
                    note=note,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer progress 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_progress",
                exc=exc,
                discord_module=discord,
            )

    @bot.tree.command(
        name="engineer_complete",
        description="engineering-agent 세션을 완료 상태로 닫고 요약을 게시합니다.",
        guild=guild,
    )
    @app_commands.describe(
        session_id="완료 처리할 워크플로 세션 ID.",
        summary="완료 보고에 들어갈 요약 (한두 줄).",
    )
    async def engineer_complete(
        interaction: "discord.Interaction",
        session_id: str,
        summary: str,
    ) -> None:
        try:
            if not await _safe_defer(interaction, discord_module=discord):
                return
            try:
                message = await asyncio.to_thread(
                    _run_engineer_complete,
                    session_id=session_id,
                    summary=summary,
                )
            except (WorkflowError, ValueError) as exc:
                await _send_message_chunks(
                    interaction,
                    f"engineer complete 실패: {exc}",
                    allowed_mentions=allowed_mentions,
                    discord_module=discord,
                )
                return
            await _send_message_chunks(
                interaction,
                message,
                allowed_mentions=allowed_mentions,
                discord_module=discord,
            )
        except Exception as exc:  # noqa: BLE001 - broad: must surface so Discord stops showing timeout
            await _surface_unexpected_engineer_error(
                interaction,
                command_name="engineer_complete",
                exc=exc,
                discord_module=discord,
            )


def _engineer_orchestrator() -> WorkflowOrchestrator:
    repo_root = Path(os.environ.get("YULE_REPO_ROOT", ".")).resolve()
    pool = build_participants_pool(repo_root, "engineering-agent")
    return WorkflowOrchestrator(Dispatcher(pool))


def _run_engineer_intake(
    *,
    prompt: str,
    task_type: Optional[str],
    write_requested: bool,
    channel_id: Optional[int],
    user_id: Optional[int],
):
    parsed: Optional[TaskType] = None
    if task_type:
        try:
            parsed = TaskType(task_type)
        except ValueError as exc:
            raise ValueError(
                f"task_type must be one of {[t.value for t in TaskType]}, got {task_type!r}"
            ) from exc
    orchestrator = _engineer_orchestrator()
    result = orchestrator.intake(
        prompt=prompt,
        task_type=parsed,
        write_requested=write_requested,
        channel_id=channel_id,
        user_id=user_id,
    )

    # P1-M A — slash command path 도 session.extra 에 work_mode /
    # topology / scope / mode_decided_* 를 영속해야 한다. 옛 wiring 은
    # 채널 router path 만 ``prepare_coding_session_context`` 를 거쳤기
    # 때문에 `/engineer_intake` 슬래시 세션은 work_mode=None 으로 떨어졌고,
    # background pr_merge_continuation_loop 가 approval_required 로 fallback
    # 됐다 (회귀: session fe5eedc65196).
    try:
        _persist_intake_mode_and_backlog(
            session=result.session, prompt_text=prompt
        )
    except Exception:  # noqa: BLE001 - never block intake response
        import logging

        logging.getLogger(__name__).warning(
            "engineer_intake: mode/backlog 영속화 실패 — 본문 응답은 그대로 진행",
            exc_info=True,
        )

    # P0-T smoke fix (session c5278a9043f2 repro):
    # /engineer_intake 슬래시 명령이 issue-less full-stack coding 요청을
    # 받았을 때 본문 응답만 게시하고 `#승인-대기` 카드는 누락되던 회귀
    # 차단. write_requested=True 이고 stack_detector / phrase_detect 가
    # coding intent 를 인식하면 ApprovalWorker 빌드 후
    # ``enqueue_github_work_approval`` 호출. 실패는 swallow — slash
    # command 본문 응답은 절대 막지 않는다.
    if write_requested:
        try:
            _maybe_post_intake_approval_card(
                session=result.session,
                prompt_text=prompt,
                requested_by=str(user_id or ""),
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "engineer_intake: approval card posting 실패 — 본문 응답은 그대로 진행",
                exc_info=True,
            )
    return result


def _persist_intake_mode_and_backlog(
    *, session: Any, prompt_text: str
) -> None:
    """P1-M — slash command intake 직후 mode/backlog 영속화.

    1. ``prepare_coding_session_context`` 로 work_mode/topology/scope
       /mode_decided_* 계산 후 session.extra 머지.
    2. full_stack_single_repo 의도면 ``seed_coding_backlog`` 호출해
       backlog 8 개 slice 를 stamp (이미 있으면 보존).
    """

    if session is None:
        return
    try:
        from ..engineering_channel_router.session_persistence import (
            _persist_coding_session_context,
        )
    except Exception:  # noqa: BLE001 - partial install
        return

    refs = list(getattr(session, "references_user", None) or ())
    user_links = tuple(str(r) for r in refs)
    _persist_coding_session_context(
        session,
        message_text=prompt_text or "",
        user_links=user_links,
    )

    try:
        from yule_orchestrator.agents.coding.coding_backlog_seed import seed_coding_backlog

        seed_coding_backlog(session_id=getattr(session, "session_id", None))
    except Exception:  # noqa: BLE001
        return


def _ensure_coding_proposal_on_session(session: Any, prompt_text: str) -> None:
    """If *session.extra* lacks ``coding_proposal``, build one from
    *prompt_text* and persist it. Idempotent — existing payload wins.

    P0-W slash intake fix — engineering channel router 의 coding gate 가
    "코딩 권한 제안" 단어를 받아야만 stamp 하던 것을, slash command path
    에서도 동일 stamp 가 일어나도록 한다. 결과:

      * `_maybe_post_intake_approval_card` 시점에 session.extra 에 코딩
        proposal payload 가 살아있음.
      * 이후 `promote_session_to_coding_ready` 가 anchor 만 받아도 곧장
        coding_job=ready 로 promote 가능 (no_coding_proposal noop 제거).
    """

    extra = getattr(session, "extra", None) or {}
    if isinstance(extra, Mapping) and isinstance(
        extra.get("coding_proposal"), Mapping
    ):
        return  # 이미 있음 — 덮어쓰지 않는다

    try:
        from yule_orchestrator.agents.coding.authorization import recommend_authorization
        from ..engineering_channel_router.session_persistence import (
            _persist_coding_proposal,
        )
    except Exception:  # noqa: BLE001 - partial install fallback
        return

    try:
        proposal = recommend_authorization(
            user_request=prompt_text or "",
            session_id=getattr(session, "session_id", None),
        )
    except Exception:  # noqa: BLE001 - never block intake on proposal failure
        return
    if proposal is None:
        return
    try:
        _persist_coding_proposal(session, proposal)
    except Exception:  # noqa: BLE001
        return


def _extract_repo_from_session(session: Any, prompt_text: str) -> Optional[str]:
    """Resolve canonical ``owner/repo`` from session refs or prompt text.

    Producer path (P0-T live smoke fix): without this `_maybe_post_intake_approval_card`
    builds a proposal with empty repo → work_order executor lands
    ``github_work_order_no_repo`` failed_retryable.

    Resolution order — first match wins:
      1. ``session.references_user`` (extracted from prompt by intake)
      2. ``session.extra['coding_repo_full_name']``
      3. raw scan of *prompt_text* via ``parse_github_target``

    Returns the canonical ``owner/repo`` string, or ``None`` when no
    GitHub URL fragment is found (operator did not give a repo).
    """

    try:
        from yule_orchestrator.agents.git.github_url import parse_github_target
    except Exception:  # noqa: BLE001 - partial install
        return None

    refs = list(getattr(session, "references_user", None) or ())
    extra = getattr(session, "extra", None) or {}
    if isinstance(extra, Mapping):
        existing = str(extra.get("coding_repo_full_name") or "").strip()
        if existing and "/" in existing:
            return existing

    candidates: list[str] = []
    candidates.extend(str(r) for r in refs)
    # parse_github_target only consumes one URL at a time; we'll feed it
    # any URL-looking fragment from the prompt text too.
    for token in (prompt_text or "").split():
        token = token.strip().strip("(),.;<>")
        if token.startswith(("http://", "https://", "github.com")):
            candidates.append(token)

    for raw in candidates:
        target = parse_github_target(raw)
        if target is None:
            continue
        owner = (target.owner or "").strip()
        repo = (target.repo or "").strip()
        if owner and repo:
            return f"{owner}/{repo}"
    return None


def _maybe_post_intake_approval_card(
    *,
    session: Any,
    prompt_text: str,
    requested_by: str,
) -> None:
    """Build production ApprovalWorker + post `#승인-대기` 카드 (best-effort).

    intake 시점에는 session.extra 가 비어있어 should_route_to_github_workos
    가 lifecycle_mode 누락 + coding_intent 둘 중 하나로 skip 할 수 있다.
    그래서 본 helper 는 intake 직전 session 에 ``lifecycle_mode=implementation``
    임시 stamp + ``active_research_roles`` placeholder 만 채워 adapter 가
    proposal 을 build 할 수 있게 한다.
    """

    import asyncio as _asyncio

    from yule_orchestrator.agents.job_queue import (
        ApprovalWorker,
        HeartbeatStore,
        JobQueue,
    )
    from yule_orchestrator.agents.job_queue.approval_discord_poster import (
        build_approval_channel_resolver,
        build_production_post_fn,
    )
    from ..integrations.github_workos_adapter import (
        enqueue_github_work_approval,
        should_route_to_github_workos,
    )

    # intake 직후 session.extra 는 비어있을 수 있으므로 adapter 가 인식할
    # 최소 lifecycle 신호를 임시로 채운다. 실제 lifecycle_mode 는 후속
    # `_run_coding_authorization_gate` 가 정확한 값으로 갱신.
    session_extra = dict(getattr(session, "extra", None) or {})
    if "lifecycle_mode" not in session_extra:
        session_extra["lifecycle_mode"] = "implementation"
    if "active_research_roles" not in session_extra:
        session_extra["active_research_roles"] = ["tech-lead", "backend-engineer"]
    if session_extra != (getattr(session, "extra", None) or {}):
        try:
            from dataclasses import replace as _replace
            from yule_orchestrator.agents.workflow_state import update_session as _update

            session = _update(
                _replace(session, extra=session_extra),
                now=datetime.now(),
            )
        except Exception:  # noqa: BLE001 - keep going with in-memory copy
            try:
                session.extra = session_extra  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    eligible, reason, _ = should_route_to_github_workos(
        session=session, request_text=prompt_text
    )
    if not eligible:
        return

    # P0-W — slash intake 가 coding intent 로 인정된 시점에 즉시
    # `session.extra['coding_proposal']` 를 stamp. 이전엔 engineering
    # channel router 의 `_run_coding_authorization_gate` 만 stamp 해서
    # slash command 단독 경로는 coding_proposal 이 영원히 비어 있었고,
    # work_order continuation 이 `no_coding_proposal` 으로 noop 처리됐다.
    # 본 helper 가 idempotent — 이미 있으면 skip.
    _ensure_coding_proposal_on_session(session, prompt_text)

    queue = JobQueue()
    worker = ApprovalWorker(
        queue=queue,
        heartbeats=HeartbeatStore(),
        post_fn=build_production_post_fn(),
        channel_resolver=build_approval_channel_resolver(),
    )

    # P0-T live smoke fix — repo 가 비어있어 work_order executor 가
    # `github_work_order_no_repo` failed_retryable 로 떨어지던 회귀
    # 차단. session.references_user / extra / prompt 에서 canonical
    # owner/repo 추출해 enqueue_github_work_approval 로 forwarding.
    resolved_repo = _extract_repo_from_session(session, prompt_text)

    async def _go():
        return await enqueue_github_work_approval(
            session=session,
            request_text=prompt_text,
            approval_worker=worker,
            requested_by=requested_by,
            repo=resolved_repo,
        )

    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            # Slash command path 는 _run_engineer_intake 를 to_thread 로
            # 호출하므로 별도 loop 가 필요. 새 loop 로 한 줄 실행.
            new_loop = _asyncio.new_event_loop()
            try:
                new_loop.run_until_complete(_go())
            finally:
                new_loop.close()
        else:
            loop.run_until_complete(_go())
    except RuntimeError:
        new_loop = _asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(_go())
        finally:
            new_loop.close()


def _load_engineer_session(*, session_id: str):
    orchestrator = _engineer_orchestrator()
    return orchestrator.get(session_id)


def _run_engineer_review(
    *,
    session_id: str,
    summary: str,
    body: Optional[str],
    severity: Optional[str],
    categories: Optional[str],
    source: Optional[str],
    file_paths: Optional[str],
    channel_id: Optional[int],
    thread_id: Optional[int],
    user_id: Optional[int],
    author_name: Optional[str],
):
    if not summary or not summary.strip():
        raise ValueError("summary must not be empty")

    parsed_severity = _parse_review_severity(severity)
    parsed_source = _parse_review_source(source)

    feedback = ReviewFeedback(
        feedback_id=_generate_feedback_id(),
        source=parsed_source,
        submitted_at=datetime.now(),
        summary=summary.strip(),
        body=(body or "").strip(),
        target_session_id=session_id,
        target_thread_id=thread_id,
        file_paths=_split_csv(file_paths),
        severity=parsed_severity,
        categories=_split_csv(categories),
        author=author_name,
    )
    orchestrator = _engineer_orchestrator()
    return orchestrator.record_review_feedback(session_id, feedback)


def _run_engineer_approve(*, session_id: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id must not be empty")
    orchestrator = _engineer_orchestrator()
    session = orchestrator.approve(session_id.strip())
    return _format_engineer_approve_message(session)


def _run_engineer_reject(*, session_id: str, reason: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id must not be empty")
    if not reason or not reason.strip():
        raise ValueError("reason must not be empty")
    orchestrator = _engineer_orchestrator()
    session = orchestrator.reject(session_id.strip(), reason=reason.strip())
    return _format_engineer_reject_message(session)


def _run_engineer_progress(*, session_id: str, note: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id must not be empty")
    if not note or not note.strip():
        raise ValueError("note must not be empty")
    orchestrator = _engineer_orchestrator()
    result = orchestrator.progress(session_id.strip(), note=note.strip())
    return result.message


def _run_engineer_complete(*, session_id: str, summary: str) -> str:
    if not session_id or not session_id.strip():
        raise ValueError("session_id must not be empty")
    if not summary or not summary.strip():
        raise ValueError("summary must not be empty")
    orchestrator = _engineer_orchestrator()
    result = orchestrator.complete(session_id.strip(), summary=summary.strip())
    return result.message


def _format_engineer_approve_message(session) -> str:
    lines = [
        "**[engineering-agent] 세션 승인 완료**",
        f"세션 ID: `{session.session_id}`",
        f"상태: {session.state.value}",
        f"실행 후보: {session.executor_role} ({session.executor_runner or '?'})",
        "",
        "이제 `/engineer_progress`로 진행 메모를 남기거나 `/engineer_complete`로 마무리할 수 있습니다.",
    ]
    return "\n".join(lines)


def _format_engineer_reject_message(session) -> str:
    lines = [
        "**[engineering-agent] 세션 거절**",
        f"세션 ID: `{session.session_id}`",
        f"상태: {session.state.value}",
        f"사유: {session.rejection_reason or 'rejected'}",
        "",
        "거절된 세션은 재개할 수 없습니다. 새로 시작하시려면 채널에서 자연어로 그냥 말씀하시거나 "
        "`/engineer_intake` 로 정식 등록해 주세요. 도움이 필요하면 `/help` 로 사용법을 확인할 수 있어요.",
    ]
    return "\n".join(lines)


def _run_engineer_review_reply(
    *,
    session_id: str,
    feedback_id: str,
    applied: str,
    proposed: Optional[str],
    remaining: Optional[str],
):
    applied_items = _split_lines_or_semicolons(applied)
    if not applied_items:
        raise ValueError("applied must include at least one item")
    proposed_items = _split_lines_or_semicolons(proposed)
    remaining_items = _split_lines_or_semicolons(remaining)
    orchestrator = _engineer_orchestrator()
    return orchestrator.respond_to_review(
        session_id,
        feedback_id=feedback_id,
        applied=applied_items,
        proposed=proposed_items,
        remaining=remaining_items,
    )


def _parse_review_severity(value: Optional[str]) -> ReviewSeverity:
    if not value:
        return ReviewSeverity.MEDIUM
    try:
        return ReviewSeverity(value.strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"severity must be one of {[s.value for s in ReviewSeverity]}, got {value!r}"
        ) from exc


def _parse_review_source(value: Optional[str]) -> ReviewSource:
    if not value:
        return ReviewSource.USER
    try:
        return ReviewSource(value.strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"source must be one of {[s.value for s in ReviewSource]}, got {value!r}"
        ) from exc


def _split_csv(value: Optional[str]) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _split_lines_or_semicolons(value: Optional[str]) -> tuple[str, ...]:
    if not value:
        return ()
    parts: list[str] = []
    for chunk in value.replace(";", "\n").splitlines():
        stripped = chunk.strip().lstrip("-• ").strip()
        if stripped:
            parts.append(stripped)
    return tuple(parts)


def _generate_feedback_id() -> str:
    return f"fb-{uuid.uuid4().hex[:8]}"


def _bind_discord_runtime_globals(*, discord_module: Any, app_commands_module: Any) -> None:
    globals()["discord"] = discord_module
    globals()["app_commands"] = app_commands_module


def _build_allowed_mentions(discord_module: Any) -> Any:
    return discord_module.AllowedMentions(
        users=True,
        roles=False,
        everyone=False,
        replied_user=False,
    )


async def _safe_defer(
    interaction: "discord.Interaction",
    *,
    discord_module: Any,
) -> bool:
    try:
        await interaction.response.defer(thinking=True)
    except discord_module.NotFound:
        print(
            "warning: discord interaction expired before defer could complete "
            f"(command={getattr(interaction.command, 'name', 'unknown')}, "
            f"user_id={getattr(interaction.user, 'id', 'unknown')})"
        )
        return False
    return True


async def _send_message_chunks(
    interaction: "discord.Interaction",
    message: str,
    *,
    allowed_mentions: Any,
    discord_module: Any,
) -> None:
    chunks = split_discord_message(message)
    first_chunk, *remaining = chunks
    try:
        await interaction.followup.send(first_chunk, allowed_mentions=allowed_mentions)
        for chunk in remaining:
            await interaction.followup.send(chunk, allowed_mentions=allowed_mentions)
    except discord_module.NotFound:
        print(
            "warning: discord interaction webhook expired before followup could be delivered "
            f"(command={getattr(interaction.command, 'name', 'unknown')}, "
            f"user_id={getattr(interaction.user, 'id', 'unknown')})"
        )


async def _surface_unexpected_engineer_error(
    interaction: "discord.Interaction",
    *,
    command_name: str,
    exc: BaseException,
    discord_module: Any,
) -> None:
    """Surface an unexpected exception via the Discord followup channel.

    Without this, broad exceptions from ``/engineer_*`` handlers bubble
    out before Discord receives any followup, which the Discord client
    displays as a generic "애플리케이션이 응답하지 않았어요" timeout.
    Operators then have no signal as to which command failed or why.

    Best-effort: we try ``followup.send`` first (interaction was already
    deferred in the happy path), fall back to ``response.send_message``
    if it wasn't, and as a last resort log to stderr so the failure
    isn't silent.
    """

    del discord_module  # API parity with other helpers; not needed here.
    text = (
        f"⚠️ `/{command_name}` 처리 중 예상치 못한 오류가 발생했어요.\n"
        f"`{type(exc).__name__}`: {exc}"
    )
    delivered = False
    try:
        await interaction.followup.send(text)
        delivered = True
    except Exception:  # noqa: BLE001 - fall through to response.send_message
        pass
    if not delivered:
        try:
            await interaction.response.send_message(text)
            delivered = True
        except Exception:  # noqa: BLE001 - last-resort logging below
            pass
    if not delivered:
        print(
            "error: failed to surface unexpected /"
            f"{command_name} error to Discord: {type(exc).__name__}: {exc}"
        )
