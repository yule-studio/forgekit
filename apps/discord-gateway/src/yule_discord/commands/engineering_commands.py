"""Engineering-gateway owned ``/help`` and ``/engineer_*`` slash commands.

Split out of ``commands/__init__.py`` (command-group split). The facade
in ``__init__`` keeps the public ``register_*`` API and the engineering
backend (``_run_engineer_*`` / approval-card plumbing); this module holds
only the slash-command registration body so the engineering command
surface is one cohesive unit.

The backend ``_run_engineer_*`` helpers are imported lazily from the
package ``__init__`` inside the registration body — both to avoid an
import-time cycle (the facade imports this module for registration) and
to keep test patches on ``yule_discord.commands._run_engineer_*`` /
``_maybe_post_intake_approval_card`` effective at call time.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from yule_engineering.agents import WorkflowError
from ..engineering.help_surface import render_engineer_help_message
from ._discord_helpers import (
    _safe_defer,
    _send_message_chunks,
    _surface_unexpected_engineer_error,
)


def _register_engineering_commands_impl(
    bot: "commands.Bot",
    *,
    guild: Any,
    allowed_mentions: Any,
    discord: Any,
    app_commands: Any,
) -> None:
    from . import (
        _load_engineer_session,
        _run_engineer_approve,
        _run_engineer_complete,
        _run_engineer_intake,
        _run_engineer_progress,
        _run_engineer_reject,
        _run_engineer_review,
        _run_engineer_review_reply,
    )

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
