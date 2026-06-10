"""Slash-command facade for the Discord gateway.

This module keeps the public registration API (``register_discord_commands``
and the planning-/engineering-only wrappers, ``BotRoleSet`` /
``resolve_bot_role_set_from_env``) plus the engineering backend logic
(``_run_engineer_*`` and the intake approval-card plumbing). The actual
per-group slash-command registration bodies live in sibling modules
(command-group split):

* :mod:`._discord_helpers` — shared interaction transport helpers.
* :mod:`.planning_commands` — ``_register_planning_commands_impl``.
* :mod:`.engineering_commands` — ``_register_engineering_commands_impl``.

The engineering backend stays here (rather than alongside the
registration body) so test patches on
``yule_discord.commands._run_engineer_*`` /
``_maybe_post_intake_approval_card`` keep resolving against this module.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional

from yule_engineering.agents import (
    Dispatcher,
    TaskType,
    WorkflowOrchestrator,
    build_participants_pool,
)
from yule_engineering.agents.review_loop import (
    ReviewFeedback,
    ReviewSeverity,
    ReviewSource,
)
from .engineering_commands import _register_engineering_commands_impl
from .planning_commands import _register_planning_commands_impl


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
        from yule_engineering.agents.coding.coding_backlog_seed import seed_coding_backlog

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
        from yule_engineering.agents.coding.authorization import recommend_authorization
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
        from yule_vcs.github_url import parse_github_target
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

    from yule_engineering.agents.job_queue import (
        ApprovalWorker,
        HeartbeatStore,
        JobQueue,
    )
    from yule_engineering.agents.job_queue.approval_discord_poster import (
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
            from yule_engineering.agents.workflow_state import update_session as _update

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


# Re-export shared Discord interaction helpers so existing importers /
# test patches against ``yule_discord.commands._safe_defer`` etc. keep
# resolving after the command-group split moved them to a sibling module.
from ._discord_helpers import (  # noqa: E402
    _safe_defer,
    _send_message_chunks,
    _surface_unexpected_engineer_error,
)

