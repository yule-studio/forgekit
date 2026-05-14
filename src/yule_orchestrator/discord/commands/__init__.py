from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from ...agents import (
    Dispatcher,
    TaskType,
    WorkflowError,
    WorkflowOrchestrator,
    build_participants_pool,
)
from ...agents.review_loop import (
    ReviewFeedback,
    ReviewSeverity,
    ReviewSource,
)
from ..ui.formatter import (
    format_checkpoints_message,
    format_plan_today_message,
    format_snapshot_regenerating_message,
    format_snapshot_regeneration_failed_message,
    split_discord_message,
)
from ..runtime.planning import build_due_checkpoints, load_plan_today_snapshot


def register_discord_commands(
    bot: "commands.Bot",
    guild_id: int,
    notify_user_id: int | None = None,
) -> None:
    import discord
    from discord import app_commands

    _bind_discord_runtime_globals(discord_module=discord, app_commands_module=app_commands)
    guild = discord.Object(id=guild_id)
    allowed_mentions = _build_allowed_mentions(discord)

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
    return orchestrator.intake(
        prompt=prompt,
        task_type=parsed,
        write_requested=write_requested,
        channel_id=channel_id,
        user_id=user_id,
    )


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
        "거절된 세션은 재개할 수 없습니다. 필요하면 `/engineer_intake`로 새 세션을 시작해주세요.",
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
