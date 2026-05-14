"""engineering_channel_router — "저장 승인" / "이대로 저장" gate.

User types "저장 승인" / "이대로 저장" in #업무-접수 → the router
must run the Obsidian save workflow before falling through to the
default new-work classifier (which would intake a brand-new session
with the save phrase as ``session.prompt``).

Also handles the preview-before-save branch: when the runtime preflight
classified the message as ``EXECUTE_EXISTING_STEP`` + ``is_obsidian_save_request``
+ a matched session, we build the preview *before* a join so the user
can confirm what we would write.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from .models import EngineeringRouteResult, SendChunksFn
from .session_persistence import (
    _is_terminal,
    _load_session_by_id,
    _most_recent_session,
)
from ...agents.lifecycle.resolver import (
    _EXPLICIT_SESSION_ID_RE,
    extract_explicit_session_id as _extract_session_id_from_router_text,
)
from ...agents.obsidian.approval import (
    ObsidianApprovalError,
    build_save_proposal,
    clear_pending_proposal,
    execute_pending_proposal,
    get_pending_proposal,
    is_obsidian_approval,
    is_obsidian_save_request,
    store_pending_proposal,
)


def _can_save_to_obsidian(session: Any) -> tuple[bool, Optional[str]]:
    """Return (allowed, blocking_reason).

    Phase 4 stab: an Obsidian write must NOT proceed when the
    lifecycle hasn't actually closed. Reads ``session.extra`` for the
    Phase 2/3 status keys and refuses if research is empty / forum
    isn't connected / work_report is not ready/final.

    Returns ``(True, None)`` to allow, ``(False, "<korean reason>")``
    to block. Test stubs that don't carry rich extras get a generous
    "missing canonical readiness" reason rather than a hard pass.
    """

    # Refactor: delegate to the canonical :mod:`agents.lifecycle.status`
    # helper so the router, work_report builder, and Discord status
    # diagnostic all share one set of "can we save?" rules. The block
    # reasons stay identical to keep operator-visible messages stable.
    from ...agents.lifecycle.status import can_write_obsidian_record

    return can_write_obsidian_record(session)

async def _run_obsidian_approval_gate(
    *,
    message: Any,
    prompt_text: str,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
    writer_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Any] = None,
) -> Optional[EngineeringRouteResult]:
    """Try to interpret *prompt_text* as an Obsidian save approval.

    Phase 4 stab: explicit "세션 <id> 기준으로 저장해줘" prompts now
    resolve via the id first (load_session) — channel/thread walks
    only fire when the user didn't name a session. Final write is
    blocked when the lifecycle is incomplete (no research_pack,
    interim/insufficient work_report, missing role coverage).

    Returns a populated :class:`EngineeringRouteResult` when the message
    was an approval phrase (regardless of whether the write succeeded),
    or ``None`` to fall through to the runtime preflight + conversation
    flow. We deliberately keep this branch above the runtime classifier
    so a bare "저장 승인" never gets promoted to ``new_work_request``.
    """

    # Phase 4 stab: accept "세션 <id> 기준으로 저장 승인" by stripping
    # the explicit-id preamble before testing the approval phrase.
    explicit_id = _extract_session_id_from_router_text(prompt_text)
    test_text = prompt_text
    if explicit_id and not is_obsidian_approval(test_text):
        # Drop the "세션 <id> 기준으로" prefix and re-test so a
        # session-scoped approval still routes through this gate.
        stripped = _EXPLICIT_SESSION_ID_RE.sub("", prompt_text).strip()
        if stripped:
            for filler in ("기준으로", "기준 으로", "기준에서", "기준"):
                if stripped.startswith(filler):
                    stripped = stripped[len(filler):].strip()
                    break
            if is_obsidian_approval(stripped):
                test_text = stripped

    if not is_obsidian_approval(test_text):
        return None

    candidate: Optional[Any] = None
    if explicit_id:
        try:
            from ...agents.workflow_state import load_session as _load_session

            candidate = _load_session(explicit_id)
        except Exception:  # noqa: BLE001 - lookup failure falls through
            candidate = None
        if candidate is None:
            await send_chunks(
                message.channel,
                (
                    f"세션 `{explicit_id}` 을 찾지 못했어요.\n"
                    "session id 가 정확한지 확인하거나, 새 작업이라면 `새 작업으로 진행`이라고 답해 주세요."
                ),
            )
            return EngineeringRouteResult(
                handled=True,
                error=f"obsidian approval: explicit session {explicit_id} not found",
            )

    if candidate is None:
        candidate = _find_session_with_pending_proposal(
            message=message,
            list_sessions_fn=list_sessions_fn,
        )
    if candidate is None:
        await send_chunks(
            message.channel,
            (
                "지금은 대기 중인 Obsidian 저장 제안이 없어요.\n"
                "먼저 `Obsidian에 정리해줘` 처럼 저장 미리보기를 만들어 주세요."
            ),
        )
        return EngineeringRouteResult(handled=True)

    allowed, block_reason = _can_save_to_obsidian(candidate)
    if not allowed:
        await send_chunks(
            message.channel,
            (
                "Obsidian 저장을 진행하지 않았어요.\n"
                f"차단 사유: {block_reason}\n"
                "lifecycle 이 완료되면 다시 `저장 승인` 으로 답해 주세요."
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(candidate, "session_id", None),
            error=f"obsidian approval blocked: {block_reason}",
        )

    try:
        updated, outcome = execute_pending_proposal(
            candidate,
            env=env,
            writer_fn=writer_fn,
        )
    except ObsidianApprovalError as exc:
        await send_chunks(message.channel, f"⚠️ Obsidian 저장 실패: {exc}")
        return EngineeringRouteResult(handled=True, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — never let the bot crash on save
        await send_chunks(
            message.channel,
            f"⚠️ Obsidian 저장 중 예상치 못한 오류: {exc}",
        )
        return EngineeringRouteResult(handled=True, error=str(exc))

    await send_chunks(message.channel, outcome.message)
    return EngineeringRouteResult(
        handled=True,
        session_id=getattr(updated, "session_id", None),
    )

async def _run_obsidian_preview_branch(
    *,
    message: Any,
    prompt_text: str,
    decision_payload: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
) -> Optional[EngineeringRouteResult]:
    """Render the preview for an Obsidian save request and store the proposal.

    Called from :func:`_run_runtime_preflight` when the runtime intent
    classifier identified an Obsidian save request and Recall matched a
    session. We never call ``thread_continuation_fn`` here — joining the
    thread isn't the user's goal; they want to see what we'd write before
    approving it.
    """

    target_session_id: Optional[str] = None
    if hasattr(decision_payload, "get"):
        raw = decision_payload.get("session_id")
        target_session_id = str(raw) if raw is not None else None

    session = _load_session_by_id(list_sessions_fn, target_session_id)
    if session is None:
        await send_chunks(
            message.channel,
            (
                "**[engineering-agent] Obsidian 저장 대상 세션을 찾지 못했어요.**\n"
                "어떤 세션을 저장할지 `기존 세션 <id>` 처럼 답해 주세요."
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            error="obsidian preview: matched session not loadable",
        )

    try:
        proposal = build_save_proposal(
            session,
            actor_user_id=getattr(getattr(message, "author", None), "id", None),
        )
    except ObsidianApprovalError as exc:
        await send_chunks(message.channel, f"⚠️ {exc}")
        return EngineeringRouteResult(handled=True, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        await send_chunks(
            message.channel,
            f"⚠️ Obsidian 미리보기 생성 실패: {exc}",
        )
        return EngineeringRouteResult(handled=True, error=str(exc))

    try:
        store_pending_proposal(session, proposal)
    except Exception as exc:  # noqa: BLE001 — preview survives even if persist fails
        await send_chunks(
            message.channel,
            f"⚠️ 저장 제안 기록 실패: {exc}\n미리보기는 아래에서 확인하실 수 있어요.",
        )

    await send_chunks(message.channel, proposal.preview_message)
    return EngineeringRouteResult(
        handled=True,
        session_id=session.session_id,
        thread_id=getattr(session, "thread_id", None),
    )

def _find_session_with_pending_proposal(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Return the session that owns the most relevant pending proposal.

    Match priority:
      1. Sessions whose ``thread_id`` equals the message's channel id
         (for thread messages — Discord exposes the thread id as
         ``channel.id`` and the channel id as ``channel.parent_id``).
      2. Sessions in the same channel + same author with a proposal.
      3. Most recently updated session with a proposal.

    Returns ``None`` when no candidate has a pending proposal stashed in
    ``session.extra``.
    """

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001 — recall outage must not crash bot
        return None
    if not sessions:
        return None

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    if parent_id is None and getattr(channel, "parent", None) is None:
        thread_id = None
        scoped_channel_id = channel_id
    else:
        thread_id = channel_id
        scoped_channel_id = parent_id
    user_id = getattr(getattr(message, "author", None), "id", None)

    candidates_with_proposal = [
        s for s in sessions if get_pending_proposal(s) is not None
    ]
    if not candidates_with_proposal:
        return None

    if thread_id is not None:
        for session in candidates_with_proposal:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in candidates_with_proposal
            if getattr(s, "channel_id", None) == scoped_channel_id
            and (user_id is None or getattr(s, "user_id", None) == user_id)
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(candidates_with_proposal)


__all__ = (
    "_can_save_to_obsidian",
    "_run_obsidian_approval_gate",
    "_run_obsidian_preview_branch",
    "_find_session_with_pending_proposal",
)
