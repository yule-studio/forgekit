"""Operations-research forum → ApprovalRequest producer — A-M7.5.

The user complaint that closes A-M7 family: a message in
``#운영-리서치`` saying "Obsidian 에 정리하고 싶어" should produce a
``#승인-대기`` card, not silently no-op. The approval worker / reply
router / Obsidian writer have been live since A-M5/M6, but the
producer that turns a forum-thread save request into an
:class:`ApprovalRequest` was never wired.

This module is the missing producer. It is **pure-Python** — no
Discord client, no SQLite. The Discord-side adapter
(``discord/bot.py`` ``on_message``) calls
:func:`route_forum_obsidian_save_request` after a regular forum
message is observed, with all dependencies injected so the producer
stays unit-testable.

Lifecycle (matching the user spec's flow):

  1. User posts in a research forum thread: "Obsidian 에 정리하고 싶어".
  2. The phrase detector :func:`agents.obsidian.approval.is_obsidian_save_request`
     fires.
  3. We resolve the *session_id* by walking recent open sessions and
     matching ``session.extra['research_forum_thread_id']`` against
     the message's channel id.
  4. We build an :class:`ApprovalRequest` (kind = OBSIDIAN_WRITE) with
     the thread's id / title / source-message id stamped in
     ``extra``.
  5. We call :class:`ApprovalWorker.run_one` so the queue posts the
     card to ``#승인-대기`` and the row lands SAVED. Idempotency is
     handled by ``ApprovalWorker.find_active`` — same
     ``(session_id, kind, source_message_id)`` triple won't enqueue
     a second card.
  6. The Discord-side caller renders a friendly thread reply with
     "approval card 게시 완료" or "이미 같은 요청이 진행 중" based on
     the outcome.

What this module deliberately does NOT do:

  * Write to Obsidian directly. Vault writes only happen after the
    user approves in ``#승인-대기`` — that path is owned by M5a-2's
    :func:`agents.job_queue.approval_reply.handle_approval_reply`.
  * Touch ``session.extra``. The approval row carries everything
    the writer needs; mutating session state would duplicate state
    across two surfaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Tuple

from .approval_worker import (
    APPROVAL_KIND_OBSIDIAN_WRITE,
    ApprovalRequest,
    ApprovalWorker,
)


logger = logging.getLogger(__name__)


# Skipped reasons surfaced via :class:`ForumObsidianHandoffOutcome` so
# the Discord adapter / status surface can match exact strings.
SKIPPED_NOT_SAVE_REQUEST: str = "not_save_request"
SKIPPED_NO_SESSION_FOR_THREAD: str = "no_session_for_thread"
SKIPPED_DUPLICATE_APPROVAL: str = "duplicate_approval_in_flight"
SKIPPED_APPROVAL_WORKER_RAISED: str = "approval_worker_raised"
SKIPPED_APPROVAL_CHANNEL_UNSET: str = "approval_channel_unset"


# Friendly reply templates for the Discord-side adapter. Operator
# can read them from the test snapshot — they are the entire
# user-facing vocabulary the producer adds.
RESPONSE_APPROVAL_QUEUED: str = (
    "📨 Obsidian 저장 요청을 받았어요. `#승인-대기` 채널에 승인 카드를 "
    "게시했어요 (job=`{approval_job_id}`)."
)
RESPONSE_APPROVAL_DUPLICATE: str = (
    "⏳ 이 thread 의 동일 저장 요청이 이미 `#승인-대기` 큐에 들어가 있어요."
)
RESPONSE_NO_SESSION_FOR_THREAD: str = (
    "❓ 이 thread 에 연결된 세션을 찾지 못했어요. thread 가 만들어진 직후가 "
    "아니라면 `/engineer_show` 로 세션 id 를 확인해 주세요."
)
RESPONSE_APPROVAL_CHANNEL_UNSET: str = (
    "⚠️ `#승인-대기` 채널이 환경설정에 없어요. 운영자가 "
    "`DISCORD_ENGINEERING_APPROVAL_CHANNEL_*` 를 설정한 뒤 다시 시도해 주세요."
)
RESPONSE_APPROVAL_FAILED: str = (
    "⚠️ 저장 요청은 인식했지만 승인 카드 게시에 실패했어요. "
    "잠시 후 다시 시도하거나 운영자에게 알려 주세요. "
    "운영자 진단 코드는 `journalctl` 에 남깁니다."
)


# Inputs the producer doesn't own — injected so tests don't need a
# live Discord client / SQLite cache / workflow store.
SessionListerFn = Callable[..., Iterable[Any]]


@dataclass(frozen=True)
class ForumObsidianHandoffOutcome:
    """What :func:`route_forum_obsidian_save_request` decided.

    ``handled`` is True iff the message was a save request directed
    at this producer. The Discord-side caller uses it to short-circuit
    its remaining routing — when ``handled`` is True the user got a
    response (success or friendly error) and no further engineering
    routing should run.

    ``approval_job_id`` is the queue row id when posting succeeded,
    so the caller can show it in the friendly reply for cross-reference.
    """

    handled: bool
    approval_job_id: Optional[str] = None
    skipped_reason: Optional[str] = None
    response_template: Optional[str] = None
    error: Optional[str] = None


def _find_existing_approval_for_message(
    *,
    queue: Any,
    session_id: str,
    approval_kind: str,
    source_message_id: Optional[int],
) -> Optional[Any]:
    """Find any prior approval_post row (any state, including SAVED)
    keyed on the same source forum message id.

    The caller's idempotency key is the forum-thread message id —
    Discord guarantees it's unique per message. So as long as a
    prior approval row carries the same ``source_message_id`` we
    can safely reuse it instead of enqueuing a duplicate. Walks the
    session's row list once; the queue is small per-session.
    """

    if not session_id or source_message_id is None:
        return None
    try:
        rows = queue.list_for_session(session_id)
    except Exception:  # noqa: BLE001
        return None
    for row in rows or ():
        if getattr(row, "job_type", None) != "approval_post":
            continue
        payload = getattr(row, "payload", None) or {}
        if str(payload.get("approval_kind") or "") != approval_kind:
            continue
        existing_src = payload.get("source_message_id")
        try:
            if existing_src is not None and int(existing_src) == int(
                source_message_id
            ):
                return row
        except (TypeError, ValueError):
            continue
    return None


def _is_forum_thread_message(message: Any) -> bool:
    """Best-effort 'this came from a thread inside a forum channel' check.

    Discord text-channel messages have ``parent_id == None``;
    thread messages have ``parent_id`` pointing at the forum
    channel. We don't care which forum — the session-list match
    later restricts to research forum threads.
    """

    channel = getattr(message, "channel", None)
    if channel is None:
        return False
    parent_id = getattr(channel, "parent_id", None)
    parent = getattr(channel, "parent", None)
    return parent_id is not None or parent is not None


def _resolve_session_for_forum_thread(
    *,
    message: Any,
    session_lister: Optional[SessionListerFn],
) -> Optional[Any]:
    """Walk recent sessions, return the one whose
    ``session.extra['research_forum_thread_id']`` matches the
    forum-thread channel id. Returns the full session so the caller
    can read its prompt, role_sequence, etc.
    """

    if session_lister is None:
        return None
    channel = getattr(message, "channel", None)
    channel_id_raw = getattr(channel, "id", None)
    if channel_id_raw is None:
        return None
    try:
        channel_id = int(channel_id_raw)
    except (TypeError, ValueError):
        return None

    try:
        sessions = session_lister(limit=100)
    except TypeError:
        try:
            sessions = session_lister()
        except Exception:  # noqa: BLE001 - lister is best-effort
            return None
    except Exception:  # noqa: BLE001
        return None

    for session in sessions or ():
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, Mapping):
            continue
        forum_thread_id = extra.get("research_forum_thread_id")
        if forum_thread_id is None:
            continue
        try:
            if int(forum_thread_id) == channel_id:
                return session
        except (TypeError, ValueError):
            continue
    return None


def _build_approval_request(
    *,
    session: Any,
    message: Any,
    requested_by: str,
    requested_at: str,
) -> ApprovalRequest:
    """Compose the :class:`ApprovalRequest` for a forum-thread save.

    The ``extra`` mapping carries everything the approval reply
    router will need to recover the thread on approval — title +
    URL + message id — without re-walking the session list.
    """

    channel = getattr(message, "channel", None)
    thread_id = _safe_int(getattr(channel, "id", None))
    thread_name = (
        getattr(channel, "name", None)
        or getattr(channel, "_name", None)
        or ""
    )
    thread_url = (
        getattr(message, "jump_url", None)
        or _build_jump_url(getattr(channel, "guild", None), channel, message)
    )
    source_message_id = _safe_int(getattr(message, "id", None))

    extra_meta: dict[str, Any] = {
        "decision_id": f"forum-save:{getattr(session, 'session_id', '')}:"
        f"{source_message_id or ''}",
        "policy_level": "L3_HUMAN_REQUIRED",
        "source_thread_title": thread_name,
        "source_thread_url": thread_url,
        "requested_by": requested_by,
        "requested_at": requested_at,
        "origin": "research_forum_save_request",
    }

    research_pack = (getattr(session, "extra", None) or {}).get("research_pack")
    if isinstance(research_pack, Mapping):
        title_hint = research_pack.get("title")
        if isinstance(title_hint, str) and title_hint.strip():
            extra_meta["research_pack_title"] = title_hint.strip()

    summary = _build_summary(session, thread_name)
    title = _build_title(session, thread_name, research_pack)
    requested_action = (
        "현재 운영-리서치 thread 의 합의안과 자료를 Obsidian vault 에 "
        "knowledge note 로 적재합니다."
    )

    return ApprovalRequest(
        session_id=str(getattr(session, "session_id", "")),
        approval_kind=APPROVAL_KIND_OBSIDIAN_WRITE,
        title=title,
        summary=summary,
        requested_action=requested_action,
        created_by=requested_by,
        source_channel_id=_safe_int(
            getattr(channel, "parent_id", None)
            or getattr(channel, "id", None)
        ),
        source_thread_id=thread_id,
        source_message_id=source_message_id,
        extra=extra_meta,
    )


def _build_title(
    session: Any, thread_name: str, research_pack: Any
) -> str:
    if isinstance(research_pack, Mapping):
        title_hint = research_pack.get("title")
        if isinstance(title_hint, str) and title_hint.strip():
            return title_hint.strip()
    if thread_name:
        return str(thread_name).strip()
    prompt = (getattr(session, "prompt", "") or "").strip()
    if prompt:
        return prompt[:80]
    return f"세션 {getattr(session, 'session_id', '')}"


def _build_summary(session: Any, thread_name: str) -> str:
    prompt = (getattr(session, "prompt", "") or "").strip()
    if prompt:
        head = prompt[:160]
        if len(prompt) > 160:
            head += "…"
        return f"운영-리서치 thread (`{thread_name}`) — {head}"
    return f"운영-리서치 thread (`{thread_name}`) 의 합의안 저장 요청"


async def route_forum_obsidian_save_request(
    *,
    message: Any,
    text: str,
    queue: Any,
    approval_worker: ApprovalWorker,
    session_lister: Optional[SessionListerFn] = None,
    save_request_detector: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    requested_by_resolver: Optional[Callable[[Any], str]] = None,
) -> ForumObsidianHandoffOutcome:
    """Detect a save request and enqueue a #승인-대기 card via the worker.

    Returns ``handled=False`` when the message is not a save request
    or did not arrive in a forum thread — the caller falls through
    to its existing engineering routing. ``handled=True`` means the
    producer either posted a card / found a duplicate / hit a
    config error; either way the caller has shown a friendly
    response and should not run additional engineering routing.

    Idempotency: the underlying :class:`ApprovalWorker` keys on
    ``(session_id, approval_kind, source_message_id)``. The same
    forum-thread save message will never enqueue a second card
    because ``message.id`` is unique per Discord message.
    """

    detector = save_request_detector or _default_save_request_detector
    if not text or not detector(text):
        return ForumObsidianHandoffOutcome(
            handled=False, skipped_reason=SKIPPED_NOT_SAVE_REQUEST
        )

    if not _is_forum_thread_message(message):
        return ForumObsidianHandoffOutcome(
            handled=False, skipped_reason=SKIPPED_NOT_SAVE_REQUEST
        )

    session = _resolve_session_for_forum_thread(
        message=message, session_lister=session_lister
    )
    if session is None:
        return ForumObsidianHandoffOutcome(
            handled=True,
            skipped_reason=SKIPPED_NO_SESSION_FOR_THREAD,
            response_template=RESPONSE_NO_SESSION_FOR_THREAD,
        )

    requested_by = (
        requested_by_resolver(message)
        if requested_by_resolver is not None
        else _default_requested_by(message)
    )
    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
    requested_at = when.isoformat()

    request = _build_approval_request(
        session=session,
        message=message,
        requested_by=requested_by,
        requested_at=requested_at,
    )

    # A-M7.5 — durable dedup keyed on (session_id, kind, source_message_id).
    # ApprovalWorker.find_active only catches in-flight states; once a
    # row hits SAVED a re-post would otherwise enqueue a duplicate.
    # Same forum message id always maps to the same approval card.
    existing = _find_existing_approval_for_message(
        queue=queue,
        session_id=request.session_id,
        approval_kind=request.approval_kind,
        source_message_id=request.source_message_id,
    )
    if existing is not None:
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=getattr(existing, "job_id", None),
            skipped_reason=SKIPPED_DUPLICATE_APPROVAL,
            response_template=RESPONSE_APPROVAL_DUPLICATE,
        )

    try:
        outcome = await approval_worker.run_one(request)
    except Exception as exc:  # noqa: BLE001 - never let producer crash on_message
        logger.warning(
            "forum obsidian handoff: ApprovalWorker.run_one raised",
            exc_info=True,
        )
        return ForumObsidianHandoffOutcome(
            handled=True,
            skipped_reason=SKIPPED_APPROVAL_WORKER_RAISED,
            error=_short_error(exc),
            response_template=RESPONSE_APPROVAL_FAILED,
        )

    job = getattr(outcome, "job", None)
    job_id = getattr(job, "job_id", None) if job is not None else None
    skipped = getattr(outcome, "skipped_reason", None)

    if skipped == "duplicate_in_flight":
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=SKIPPED_DUPLICATE_APPROVAL,
            response_template=RESPONSE_APPROVAL_DUPLICATE,
        )
    if skipped == "approval_channel_unset":
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=SKIPPED_APPROVAL_CHANNEL_UNSET,
            response_template=RESPONSE_APPROVAL_CHANNEL_UNSET,
        )
    if skipped is not None:
        # claimed_by_other_worker / unknown — surface as friendly fail.
        return ForumObsidianHandoffOutcome(
            handled=True,
            approval_job_id=job_id,
            skipped_reason=skipped,
            response_template=RESPONSE_APPROVAL_FAILED,
            error=skipped,
        )

    return ForumObsidianHandoffOutcome(
        handled=True,
        approval_job_id=job_id,
        response_template=RESPONSE_APPROVAL_QUEUED,
    )


def render_handoff_response(
    outcome: ForumObsidianHandoffOutcome,
) -> Optional[str]:
    """Format the friendly Discord reply for *outcome*.

    Returns ``None`` when there's nothing to say (handled=False, or
    intentional silent skip). Otherwise renders the response template
    with any captured fields (job id / error string).
    """

    if not outcome.handled or outcome.response_template is None:
        return None
    text = outcome.response_template
    fields: dict[str, str] = {}
    if outcome.approval_job_id:
        fields["approval_job_id"] = outcome.approval_job_id
    if outcome.error:
        fields["error"] = outcome.error
    try:
        return text.format(**fields)
    except KeyError:
        return text


# ---------------------------------------------------------------------------
# Defaults — production wiring uses these; tests inject stubs.
# ---------------------------------------------------------------------------


def _default_save_request_detector(text: str) -> bool:
    """Lazy import of the canonical phrase detector. Keeps this
    module light when only the producer dataclasses are needed.
    """

    try:
        from ..obsidian.approval import is_obsidian_save_request
    except Exception:  # noqa: BLE001 - partial install fallback
        return False
    return bool(is_obsidian_save_request(text))


def _default_requested_by(message: Any) -> str:
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


def _build_jump_url(guild: Any, channel: Any, message: Any) -> Optional[str]:
    guild_id = _safe_int(getattr(guild, "id", None))
    channel_id = _safe_int(getattr(channel, "id", None))
    message_id = _safe_int(getattr(message, "id", None))
    if guild_id is None or channel_id is None or message_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _short_error(exc: BaseException) -> str:
    msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {msg}"[:300]


__all__ = (
    "ForumObsidianHandoffOutcome",
    "RESPONSE_APPROVAL_CHANNEL_UNSET",
    "RESPONSE_APPROVAL_DUPLICATE",
    "RESPONSE_APPROVAL_FAILED",
    "RESPONSE_APPROVAL_QUEUED",
    "RESPONSE_NO_SESSION_FOR_THREAD",
    "SKIPPED_APPROVAL_CHANNEL_UNSET",
    "SKIPPED_APPROVAL_WORKER_RAISED",
    "SKIPPED_DUPLICATE_APPROVAL",
    "SKIPPED_NO_SESSION_FOR_THREAD",
    "SKIPPED_NOT_SAVE_REQUEST",
    "render_handoff_response",
    "route_forum_obsidian_save_request",
)
