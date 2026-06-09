"""engineering_channel_router — "수정 권한 제안" / "수정 승인" gate.

Two MVP intents the user types in #업무-접수 that must NEVER fall
through to the conversation layer (which would mis-classify them as
new work):

- "코딩 권한 제안" / "수정 권한 제안" → build proposal preview,
  persist it as ``session.extra['coding_proposal']``.
- "수정 승인" / "이대로 구현 진행" / "구현 시작" → flip the pending
  proposal into a ready ``CodingJob``, persist as
  ``session.extra['coding_job']``.

Runs before the runtime preflight so the routing classifier never sees
a bare approval phrase and re-spawns the session as new work.

The phrase detection (:func:`is_coding_approval_phrase`,
:func:`is_coding_proposal_request`) lives in
``..engineering.phrase_detect``; this module owns the orchestration.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .models import EngineeringRouteResult, SendChunksFn
from .session_persistence import (
    _is_terminal,
    _persist_coding_job,
    _persist_coding_proposal,
    _proposal_to_dict,
    _proposal_from_dict,
)
from ..engineering.phrase_detect import (
    CODING_APPROVAL_PHRASES as _CODING_APPROVAL_PHRASES,
    CODING_PROPOSAL_REQUEST_PHRASES as _CODING_PROPOSAL_REQUEST_PHRASES,
    CONTINUATION_RESEARCH_KEYWORDS as _CONTINUATION_RESEARCH_KEYWORDS,
    NO_CODING_INTENT_PHRASES as _NO_CODING_INTENT_PHRASES,
    continuation_requests_research as _continuation_requests_research,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    user_explicitly_blocked_coding as _user_explicitly_blocked_coding,
)
from yule_orchestrator.agents.coding.authorization import (
    format_authorization_message,
    recommend_authorization,
)
from yule_orchestrator.agents.coding.job import (
    CodingJob,
    STATUS_READY,
    build_coding_job_from_proposal,
)


def _find_session_with_pending_coding_proposal(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Pick the session whose ``extra['coding_proposal']`` should pair
    with this approval phrase. Mirrors ``_find_session_with_pending_proposal``
    but reads the coding key instead of the obsidian key."""

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
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

    candidates = [
        s
        for s in sessions
        if isinstance(getattr(s, "extra", None), Mapping)
        and dict(getattr(s, "extra")).get("coding_proposal")
    ]
    if not candidates:
        return None

    if thread_id is not None:
        for session in candidates:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in candidates
            if getattr(s, "channel_id", None) == scoped_channel_id
            and (user_id is None or getattr(s, "user_id", None) == user_id)
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(candidates)

def _find_latest_open_session(
    *,
    message: Any,
    list_sessions_fn: Callable[..., Sequence[Any]],
) -> Optional[Any]:
    """Pick the session a coding proposal should target when the user
    didn't reference one explicitly. Same channel/thread > same channel
    > most recently updated open session."""

    try:
        try:
            sessions = list_sessions_fn(limit=50)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
        return None
    if not sessions:
        return None

    open_sessions = [s for s in sessions if not _is_terminal(s)]
    if not open_sessions:
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

    if thread_id is not None:
        for session in open_sessions:
            if getattr(session, "thread_id", None) == thread_id:
                return session

    if scoped_channel_id is not None:
        same_scope = [
            s
            for s in open_sessions
            if getattr(s, "channel_id", None) == scoped_channel_id
        ]
        if same_scope:
            return _most_recent_session(same_scope)

    return _most_recent_session(open_sessions)


# P0-P step 6: session.extra mutations + load helpers extracted to .session_persistence.
from .session_persistence import (  # noqa: E402,F401 — re-export for back-compat
    _is_terminal,
    _load_session_by_id,
    _most_recent_session,
    _persist_coding_job,
    _persist_coding_proposal,
    _persist_coding_session_context,
    _persist_extra_keys,
    _persist_lifecycle_mode,
    _persist_role_selection,
    _persist_thread_id,
    _proposal_from_dict,
    _proposal_to_dict,
    _record_persistence_failure,
    _work_report_to_dict,
)

async def _run_coding_authorization_gate(
    *,
    message: Any,
    prompt_text: str,
    list_sessions_fn: Callable[..., Sequence[Any]],
    send_chunks: SendChunksFn,
    approval_worker: Any = None,
) -> Optional[EngineeringRouteResult]:
    """Two-branch gate.

    1. ``is_coding_proposal_request`` — build a fresh proposal and
       stash it under ``session.extra['coding_proposal']``, then post
       the preview. The user follows up with an approval phrase.
    2. ``is_coding_approval_phrase`` — flip the latest stashed
       proposal into a ``CodingJob`` (status=ready) and persist under
       ``session.extra['coding_job']``.

    Returns ``None`` when the message isn't either kind so the caller
    falls through to the rest of the route.
    """

    # Hard "no code change" override: if the user explicitly said
    # "코드 수정 하지 말고 리서치만" the coding gate must not act on
    # this message even if it also contains a proposal/approval phrase.
    if _user_explicitly_blocked_coding(prompt_text):
        return None

    if is_coding_proposal_request(prompt_text):
        target = _find_latest_open_session(
            message=message,
            list_sessions_fn=list_sessions_fn,
        )
        if target is None:
            await send_chunks(
                message.channel,
                (
                    "현재 채널에 매칭되는 열린 engineering-agent 세션이 보이지 않아요.\n"
                    "먼저 작업을 접수해서 세션을 만들고 다시 `코딩 권한 제안`이라고 답해 주세요."
                ),
            )
            return EngineeringRouteResult(handled=True)

        proposal = recommend_authorization(
            user_request=getattr(target, "prompt", "") or "",
            session_id=getattr(target, "session_id", None),
        )
        _persist_coding_proposal(target, proposal)
        await send_chunks(message.channel, format_authorization_message(proposal))

        # P0-T smoke fix (session c5278a9043f2 repro): approval card 가
        # 본문 채널에만 뜨고 `#승인-대기` 에는 안 뜨던 회귀 차단. caller 가
        # approval_worker 를 inject 했으면 coding_proposal stamp 직후 자동
        # enqueue. worker 미주입 시 기존 동작 그대로 (회귀 없음).
        if approval_worker is not None and proposal.approval_required:
            try:
                from ..integrations.github_workos_adapter import (
                    enqueue_github_work_approval,
                )

                outcome = await enqueue_github_work_approval(
                    session=target,
                    request_text=getattr(target, "prompt", "") or prompt_text,
                    approval_worker=approval_worker,
                    source_channel_id=getattr(getattr(message, "channel", None), "id", None),
                    source_thread_id=getattr(getattr(message, "channel", None), "id", None),
                    source_message_id=getattr(message, "id", None),
                    requested_by=str(getattr(getattr(message, "author", None), "id", "")),
                )
                if outcome.proposal is not None and outcome.skipped_reason is None:
                    await send_chunks(
                        message.channel,
                        f"📨 `#승인-대기` 카드 게시 완료 (job=`{outcome.approval_job_id or '-'}`).",
                    )
                elif outcome.skipped_reason == "duplicate_approval_in_flight":
                    await send_chunks(
                        message.channel,
                        "ℹ️ 같은 작업의 `#승인-대기` 카드가 이미 게시돼 있어요.",
                    )
                # 기타 skipped_reason 은 본문 응답을 추가하지 않음 — 본문
                # proposal preview 자체로 operator 가 진행 가능.
            except Exception:  # noqa: BLE001 — never block coding gate
                # 카드 게시 실패는 본문 proposal preview 를 막지 않는다.
                # session.extra audit 에는 남기지 않음 (logger 만).
                import logging

                logging.getLogger(__name__).warning(
                    "coding_authorization_gate: enqueue_github_work_approval 실패 — 본문 proposal 만 게시",
                    exc_info=True,
                )

        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(target, "session_id", None),
            thread_id=getattr(target, "thread_id", None),
        )

    if is_coding_approval_phrase(prompt_text):
        owner = _find_session_with_pending_coding_proposal(
            message=message,
            list_sessions_fn=list_sessions_fn,
        )
        if owner is None:
            await send_chunks(
                message.channel,
                (
                    "지금은 대기 중인 코딩 권한 제안이 없어요.\n"
                    "먼저 `코딩 권한 제안` 이라고 답해서 Tech Lead 추천을 받아 주세요."
                ),
            )
            return EngineeringRouteResult(handled=True)

        extra = dict(getattr(owner, "extra", {}) or {})
        payload = extra.get("coding_proposal")
        if not isinstance(payload, Mapping):
            await send_chunks(
                message.channel,
                "대기 중인 코딩 권한 제안 payload를 읽지 못했어요. 다시 `코딩 권한 제안`을 시도해 주세요.",
            )
            return EngineeringRouteResult(handled=True)

        from datetime import datetime as _dt
        from datetime import timezone as _tz

        approved_at = _dt.now(_tz.utc)
        proposal = _proposal_from_dict(payload)
        try:
            job = build_coding_job_from_proposal(
                proposal,
                status=STATUS_READY,
                approved_at=approved_at,
            )
        except Exception as exc:  # noqa: BLE001
            await send_chunks(
                message.channel,
                f"⚠️ 코딩 권한 승인 중 오류가 발생했어요: {exc}",
            )
            return EngineeringRouteResult(handled=True, error=str(exc))

        _persist_coding_job(owner, job.to_dict())

        thread_label = (
            f"thread `{job.session_id}`"
            if job.session_id
            else "(session id 미기록)"
        )
        await send_chunks(
            message.channel,
            "\n".join(
                [
                    "**[engineering-agent] 코딩 권한 승인 완료**",
                    "",
                    f"executor: `{job.executor_role}`",
                    f"세션: {thread_label}",
                    f"승인 시각: {approved_at.isoformat()}",
                    "",
                    "이제 executor에게 안전한 prompt가 전달될 준비가 됐어요. 실제 코드 변경은 executor가 계획을 보여 드린 뒤에만 진행합니다.",
                ]
            ),
        )
        return EngineeringRouteResult(
            handled=True,
            session_id=getattr(owner, "session_id", None),
            thread_id=getattr(owner, "thread_id", None),
        )

    return None


# MVP closure refactor — explicit session id regex moved to
# :mod:`agents.lifecycle.resolver` so router / bot / obsidian gate
# share one canonical implementation. The router-private alias is
# kept for backward compat with internal callers (and the runtime
# preflight ``_explicit_session_id`` substring check).
from yule_orchestrator.agents.lifecycle.resolver import (
    _EXPLICIT_SESSION_ID_RE as _EXPLICIT_SESSION_ID_RE,
    extract_explicit_session_id as _extract_session_id_from_router_text,
)


__all__ = (
    "_find_session_with_pending_coding_proposal",
    "_find_latest_open_session",
    "_run_coding_authorization_gate",
)
