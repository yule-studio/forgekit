"""Discord adapter that routes ``#승인-대기`` replies into the
queue's :func:`handle_approval_reply` — A-M6.1b-2.

Pure-Python helper. ``bot.py`` 's ``on_message`` calls
:func:`route_approval_channel_message` early, before the
engineering route. When the message arrives in the configured
approval channel, this helper:

  1. Resolves the matching ``approval_post`` job by walking each
     open WorkflowSession (the user's reply usually doesn't carry
     a session id; we scan recent open sessions).
  2. Calls :func:`handle_approval_reply` with the parsed reply +
     the user / channel / message ids.
  3. Renders a short friendly response via *send_chunks*.

If the message isn't in the approval channel the function returns
``handled=False`` so ``on_message`` falls through to its existing
engineering routing — the legacy in-channel approval UX is
untouched.

The helper is sync-friendly: every Discord-side dependency
(channel id matchers, send_chunks, session_lister) is injected
so unit tests can drive every branch without a real Discord
client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

from ...agents.job_queue.approval_reply import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalIntent,
    ApprovalReplyOutcome,
    handle_approval_reply,
    parse_approval_intent,
)
from ...agents.job_queue.obsidian_writer_worker import ObsidianWriterWorker
from ...agents.job_queue.store import JobQueue


logger = logging.getLogger(__name__)


# Friendly response templates. Operator can read these directly
# from the test snapshots — they are the entire user-facing
# vocabulary the queue path adds, separate from the legacy
# in-channel approval UX.
RESPONSE_APPROVED: str = (
    "✅ 승인 받았어요. Obsidian 저장 큐에 넣었습니다 (job=`{write_job_id}`)."
)
RESPONSE_DUPLICATE: str = (
    "⏳ 이미 같은 결정이 저장 큐에 들어가 있어요. 기존 job 을 그대로 진행합니다."
)
RESPONSE_REJECTED: str = (
    "🚫 반려 처리했어요. 저장하지 않도록 audit 에 기록했습니다."
)
RESPONSE_NO_MATCH: str = (
    "❓ 답신에 매칭되는 승인 카드를 못 찾았어요. 카드 게시 후 30 분 안에 답해 주세요."
)
RESPONSE_HOLD_OR_UNCLEAR: str = (
    "⏸ 승인/반려 의도를 확인할 수 없어요. `승인` / `이대로 진행` 또는 `반려` 로 답해 주세요."
)
RESPONSE_UNSUPPORTED_KIND: str = (
    "ℹ️ 이 승인 유형은 아직 자동 처리하지 않아요. 운영자가 별도로 처리합니다."
)
RESPONSE_REJECTION_AUDIT_FAILED: str = (
    "⚠️ 반려는 인식했지만 audit 기록에 실패했어요. 운영자에게 다시 알려 주세요."
)


# Inputs the adapter doesn't own — injected so tests can drive the
# behaviour without a real Discord client / live SQLite session.
SessionListerFn = Callable[[], Iterable[Any]]
SendChunksFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ApprovalReplyRouteResult:
    """What ``on_message`` learned from this helper.

    ``handled=True`` means the helper either replied to the user
    or recognised the channel as the approval channel and chose
    a deliberate no-op. The caller must NOT fall through to the
    engineering route in that case — replies in the approval
    channel are not engineering intake.
    """

    handled: bool
    outcome: Optional[ApprovalReplyOutcome] = None
    response_sent: Optional[str] = None
    skipped_reason: Optional[str] = None


def is_approval_channel_message(
    *,
    message: Any,
    approval_channel_id: Optional[int],
    approval_channel_name: Optional[str] = None,
) -> bool:
    """ID-first, NAME-fallback channel matcher.

    NAME match is loose ("contains") so the same channel renamed
    from "승인-대기" to "승인 대기" doesn't break routing — the
    operator-facing convention only changes id, name is human
    readable.
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    channel_id = getattr(channel, "id", None)
    if approval_channel_id is not None and channel_id is not None:
        try:
            if int(channel_id) == int(approval_channel_id):
                return True
        except (TypeError, ValueError):
            pass
    if approval_channel_name:
        channel_name = (
            getattr(channel, "name", None)
            or getattr(channel, "_name", None)
            or ""
        )
        if approval_channel_name.strip() and approval_channel_name.strip() in str(channel_name):
            return True
    return False


async def route_approval_channel_message(
    *,
    message: Any,
    bot_user: Any,
    queue: JobQueue,
    obsidian_worker: ObsidianWriterWorker,
    approval_channel_id: Optional[int],
    approval_channel_name: Optional[str] = None,
    session_lister: Optional[SessionListerFn] = None,
    send_chunks: SendChunksFn,
    now_iso: Optional[str] = None,
) -> ApprovalReplyRouteResult:
    """Inspect *message* for an approval reply in the approval
    channel; if so, route through :func:`handle_approval_reply`
    and reply.

    Returns ``handled=False`` for messages outside the approval
    channel so ``on_message`` keeps its existing fall-through to
    the engineering route. Bot's own messages are dropped silently
    (handled=False) so a friendly reply doesn't trigger a recursive
    "approve" detection.
    """

    if getattr(getattr(message, "author", None), "bot", False):
        return ApprovalReplyRouteResult(handled=False)

    if not is_approval_channel_message(
        message=message,
        approval_channel_id=approval_channel_id,
        approval_channel_name=approval_channel_name,
    ):
        return ApprovalReplyRouteResult(handled=False)

    text = str(getattr(message, "content", "") or "").strip()
    if not text:
        return ApprovalReplyRouteResult(
            handled=True, skipped_reason="empty_message"
        )

    intent = parse_approval_intent(text)
    if intent in (ApprovalIntent.HOLD, ApprovalIntent.UNCLEAR):
        # The helper still calls handle_approval_reply for
        # symmetry, but we know the outcome will be a no-op so
        # we save the cost of the SQLite scan.
        await send_chunks(message.channel, RESPONSE_HOLD_OR_UNCLEAR)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=RESPONSE_HOLD_OR_UNCLEAR,
            skipped_reason="intent_not_actionable",
        )

    session_id = _resolve_session_for_reply(
        message=message,
        session_lister=session_lister,
    )
    if not session_id:
        await send_chunks(message.channel, RESPONSE_NO_MATCH)
        return ApprovalReplyRouteResult(
            handled=True,
            outcome=None,
            response_sent=RESPONSE_NO_MATCH,
            skipped_reason="no_session_for_reply",
        )

    approved_by = _author_handle(message)
    source_message_id = _safe_int(getattr(message, "id", None))
    source_thread_id = _safe_int(
        getattr(getattr(message, "channel", None), "id", None)
    )

    outcome = handle_approval_reply(
        queue=queue,
        obsidian_worker=obsidian_worker,
        text=text,
        session_id=session_id,
        approved_by=approved_by,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        approved_at=now_iso,
    )

    response = _render_outcome_message(outcome)
    if response is not None:
        await send_chunks(message.channel, response)

    return ApprovalReplyRouteResult(
        handled=True,
        outcome=outcome,
        response_sent=response,
        skipped_reason=outcome.skipped_reason,
    )


def _render_outcome_message(outcome: ApprovalReplyOutcome) -> Optional[str]:
    if outcome.intent == ApprovalIntent.APPROVE:
        if outcome.write_job_id is not None and outcome.skipped_reason is None:
            return RESPONSE_APPROVED.format(write_job_id=outcome.write_job_id)
        if outcome.skipped_reason == "duplicate_obsidian_write":
            return RESPONSE_DUPLICATE
        if outcome.skipped_reason == "no_matching_approval":
            return RESPONSE_NO_MATCH
        if outcome.skipped_reason == "approval_kind_not_handled":
            return RESPONSE_UNSUPPORTED_KIND
        # Fallback — shouldn't normally happen.
        return RESPONSE_NO_MATCH

    if outcome.intent == ApprovalIntent.REJECT:
        if outcome.skipped_reason == "no_matching_approval":
            return RESPONSE_NO_MATCH
        if outcome.rejection_recorded:
            return RESPONSE_REJECTED
        return RESPONSE_REJECTION_AUDIT_FAILED

    # HOLD / UNCLEAR are short-circuited before this runs; surface
    # the conservative response if we ever land here.
    return RESPONSE_HOLD_OR_UNCLEAR


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def _resolve_session_for_reply(
    *,
    message: Any,
    session_lister: Optional[SessionListerFn],
) -> Optional[str]:
    """Pick the session whose approval card the reply most likely
    targets.

    The reply text rarely carries a session id, so we walk the
    list of recent open sessions and return the first one whose
    ``thread_id`` matches the message's source channel id (the
    user replied in the work thread) or whose
    ``research_forum_thread_id`` matches. Falls back to the most
    recent open session for the same channel.

    The lister is injected so tests don't need a workflow store.
    Production wiring (``bot.py``) passes a closure around the
    real ``list_sessions`` query.
    """

    if session_lister is None:
        return None
    channel_id = _safe_int(
        getattr(getattr(message, "channel", None), "id", None)
    )
    sessions = list(session_lister() or ())
    if not sessions:
        return None

    if channel_id is not None:
        for session in sessions:
            thread_id = _safe_int(getattr(session, "thread_id", None))
            if thread_id is not None and thread_id == channel_id:
                sid = _safe_str(getattr(session, "session_id", None))
                if sid:
                    return sid
        for session in sessions:
            extra = getattr(session, "extra", None) or {}
            forum_id = _safe_int(
                extra.get("research_forum_thread_id")
                if isinstance(extra, dict)
                else None
            )
            if forum_id is not None and forum_id == channel_id:
                sid = _safe_str(getattr(session, "session_id", None))
                if sid:
                    return sid

    # Fallback — most recently updated session. Discord channels
    # like ``#승인-대기`` are global, not per-thread, so walking by
    # update order is the next-best pointer.
    most_recent = max(
        sessions,
        key=lambda s: _safe_str(getattr(s, "updated_at", "")) or "",
        default=None,
    )
    if most_recent is None:
        return None
    return _safe_str(getattr(most_recent, "session_id", None))


def _author_handle(message: Any) -> str:
    author = getattr(message, "author", None)
    if author is None:
        return "unknown"
    name = (
        getattr(author, "global_name", None)
        or getattr(author, "name", None)
        or getattr(author, "display_name", None)
    )
    user_id = getattr(author, "id", None)
    if name and user_id is not None:
        return f"{name} ({user_id})"
    if name:
        return str(name)
    if user_id is not None:
        return f"user:{user_id}"
    return "unknown"


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "ApprovalReplyRouteResult",
    "RESPONSE_APPROVED",
    "RESPONSE_DUPLICATE",
    "RESPONSE_HOLD_OR_UNCLEAR",
    "RESPONSE_NO_MATCH",
    "RESPONSE_REJECTED",
    "RESPONSE_REJECTION_AUDIT_FAILED",
    "RESPONSE_UNSUPPORTED_KIND",
    "is_approval_channel_message",
    "route_approval_channel_message",
)
