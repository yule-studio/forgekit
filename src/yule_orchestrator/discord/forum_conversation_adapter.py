"""Forum-thread conversational follow-up — P0-F branch 4.

The forum_message_adapter (branches 1+2) only handled Obsidian save
requests and active-role changes. Plain follow-up messages
("지금 뭐하고 있어?", "RAG 말고 CAG 기준으로 봐줘", "이 링크도 참고해")
fell through and arrived at no surface — the engineering channel
router treats forum threads as out-of-scope.

This adapter is **branch 4**: when the first two branches both
return ``handled=False`` (no save / no role-change intent), we
classify the message as a conversational follow-up bound to the
forum thread's existing session. The response is composed by
re-using :func:`build_engineering_conversation_response` with
``auto_collect=False`` so we never create a new intake from a
forum follow-up — the forum is for talking about *existing*
work, not filing new tasks.

Invariants:

  * Never call ``workflow.intake`` even if the conversation helper
    suggests ``ready_to_intake=True``. Drop the intent silently.
  * Never re-trigger the research collector. ``auto_collect=False``
    is hard-pinned; the follow-up should describe / correct /
    annotate the existing pack, not fetch a new one.
  * Append-context / correction directives ("이 링크도 참고해",
    "RAG 말고 CAG 기준으로") are recognized as light annotations
    persisted into ``session.extra['forum_followup_notes']`` so
    later turns / Obsidian write can see them.
  * When no session is anchored to the thread, fall through to
    ``handled=False`` so the bot can ignore the message (same
    contract as the existing role-change branch's no-session path).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence


logger = logging.getLogger(__name__)


# Skipped-reason constants.
SKIPPED_NO_SESSION_ANCHOR: str = "no_session_for_thread"
SKIPPED_HELPER_SUGGESTED_INTAKE: str = "helper_suggested_intake_dropped"
SKIPPED_NO_RESPONSE_BODY: str = "no_response_body"


# Friendly summary header (forum follow-up replies don't reuse the
# gateway's #봇-상태 status template wholesale — that template assumes
# private DM-style replies; forum threads need a tighter shape).
RESPONSE_NOTE_RECORDED: str = (
    "📝 follow-up 메모를 thread 세션에 적어 두었어요. 다음 turn 부터 "
    "각 역할 봇이 본 메모를 참고합니다."
)


@dataclass(frozen=True)
class ForumFollowupResult:
    """What the follow-up adapter decided.

    ``handled`` follows the same convention as the upstream
    ``ForumMessageRouteResult``: ``True`` short-circuits the
    on_message dispatcher, ``False`` falls through.
    """

    handled: bool
    response_sent: Optional[str] = None
    skipped_reason: Optional[str] = None
    followup_note_recorded: Optional[str] = None
    intent_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Lightweight directive detection
# ---------------------------------------------------------------------------


# "X 말고 Y" / "X 빼고 Y" / "Y 기준으로" — correction/redirect.
_CORRECTION_PATTERNS: tuple = (
    re.compile(r"(?P<wrong>\S+)\s+말고\s+(?P<right>\S+)"),
    re.compile(r"(?P<wrong>\S+)\s+빼고\s+(?P<right>\S+)"),
    re.compile(r"(?P<right>\S+)\s+기준으로"),
)


# "이 링크도 참고해" / "이것도 봐줘" / "이걸 붙여" — append-context.
_APPEND_KEYWORDS: tuple = (
    "이 링크도",
    "이것도 참고",
    "이것도 봐",
    "이걸 붙여",
    "이 자료도",
    "이 내용도",
    "이 메모도",
    "이 내용 추가",
    "참고만 해",
)


# "지금까지 합의만" / "요약만" / "결론만" — summarize directive.
_SUMMARIZE_KEYWORDS: tuple = (
    "지금까지 합의",
    "지금까지 결정",
    "결론만",
    "요약만",
    "요약해줘",
    "정리만",
)


def detect_followup_directive(text: str) -> Optional[str]:
    """Return a directive kind for *text* or ``None``.

    Recognized kinds: ``"correction"`` / ``"append"`` / ``"summarize"``.
    The adapter uses the directive label to (a) tag the note we
    persist and (b) decide whether to reach into the conversation
    helper or short-circuit with a fixed acknowledgement.
    """

    if not text:
        return None
    lowered = text.strip()
    for pattern in _CORRECTION_PATTERNS:
        if pattern.search(lowered):
            return "correction"
    for keyword in _APPEND_KEYWORDS:
        if keyword in lowered:
            return "append"
    for keyword in _SUMMARIZE_KEYWORDS:
        if keyword in lowered:
            return "summarize"
    return None


# ---------------------------------------------------------------------------
# Adapter entry point
# ---------------------------------------------------------------------------


async def handle_forum_followup(
    *,
    message: Any,
    text: str,
    session: Any,
    conversation_fn: Optional[Callable[..., Any]] = None,
    session_updater: Optional[Callable[..., Any]] = None,
    send_chunks: Optional[Callable[..., Awaitable[Any]]] = None,
) -> ForumFollowupResult:
    """Branch 4 of the forum routing — conversational follow-up.

    *session* is the existing session anchored to the thread (from
    ``_resolve_session_for_forum_thread`` in forum_message_adapter).
    When ``None`` we return ``handled=False`` so the bot drops the
    message — fall-through preserved.

    The adapter:

      1. Detects a follow-up directive (correction/append/summarize).
         If matched and the message looks like a note, persist it to
         ``session.extra['forum_followup_notes']`` and acknowledge.
      2. Otherwise dispatches to the engineering_conversation helper
         with ``auto_collect=False`` so the existing status /
         clarification / general-help responses cover the rest.
      3. Drops any ``ready_to_intake=True`` suggestion — forum is
         not an intake surface.
    """

    if session is None:
        return ForumFollowupResult(
            handled=False, skipped_reason=SKIPPED_NO_SESSION_ANCHOR
        )

    # ----------------------------------------------------------------
    # Stage 1 — light directive recognition + note persistence.
    # ----------------------------------------------------------------
    directive = detect_followup_directive(text)
    if directive is not None and session_updater is not None:
        try:
            _persist_followup_note(
                session=session,
                directive=directive,
                text=text,
                author_handle=_author_handle(message),
                session_updater=session_updater,
            )
        except Exception:  # noqa: BLE001 - persistence failure must not crash dispatch
            logger.warning(
                "forum_conversation_adapter: note persistence raised",
                exc_info=True,
            )
        if send_chunks is not None:
            try:
                await send_chunks(message.channel, RESPONSE_NOTE_RECORDED)
            except Exception:  # noqa: BLE001 - send best-effort
                logger.warning(
                    "forum_conversation_adapter: send_chunks raised on note ack",
                    exc_info=True,
                )
        return ForumFollowupResult(
            handled=True,
            response_sent=RESPONSE_NOTE_RECORDED,
            followup_note_recorded=directive,
            intent_id=f"forum_followup_{directive}",
        )

    # ----------------------------------------------------------------
    # Stage 2 — conversation helper for status / clarification / help.
    # ----------------------------------------------------------------
    helper = conversation_fn or _default_conversation_fn
    try:
        response = helper(
            message_text=text,
            author_user_id=getattr(getattr(message, "author", None), "id", None),
            mention_user=False,
            auto_collect=False,  # never new-intake from forum
            status_session_loader=lambda **_: session,
        )
    except Exception:  # noqa: BLE001 - never crash dispatch
        logger.warning(
            "forum_conversation_adapter: conversation_fn raised",
            exc_info=True,
        )
        return ForumFollowupResult(
            handled=False, skipped_reason="conversation_fn_raised"
        )

    # Forum follow-up never opens a new intake; drop the suggestion.
    if getattr(response, "ready_to_intake", False):
        return ForumFollowupResult(
            handled=False,
            skipped_reason=SKIPPED_HELPER_SUGGESTED_INTAKE,
        )

    body = getattr(response, "content", None) or ""
    if not body.strip():
        return ForumFollowupResult(
            handled=False, skipped_reason=SKIPPED_NO_RESPONSE_BODY
        )

    if send_chunks is not None:
        try:
            await send_chunks(message.channel, body)
        except Exception:  # noqa: BLE001 - best effort
            logger.warning(
                "forum_conversation_adapter: send_chunks raised on body",
                exc_info=True,
            )

    return ForumFollowupResult(
        handled=True,
        response_sent=body,
        intent_id=getattr(response, "intent_id", None),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _default_conversation_fn(**kwargs):
    """Lazy import wrapper so tests don't pay the cost when injecting."""

    from .engineering_conversation import build_engineering_conversation_response

    return build_engineering_conversation_response(**kwargs)


def _persist_followup_note(
    *,
    session: Any,
    directive: str,
    text: str,
    author_handle: str,
    session_updater: Callable[..., Any],
) -> None:
    """Append a follow-up note to ``session.extra['forum_followup_notes']``.

    Each note is a dict ``{"directive": str, "text": str,
    "author": str, "recorded_at": iso8601}``. ``session_updater``
    is the same shape as ``workflow_state.update_session``.
    """

    from dataclasses import replace as _replace

    extra_in = dict(getattr(session, "extra", None) or {})
    notes = list(extra_in.get("forum_followup_notes") or ())
    notes.append(
        {
            "directive": directive,
            "text": text.strip(),
            "author": author_handle,
            "recorded_at": datetime.now(tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        }
    )
    extra_in["forum_followup_notes"] = notes
    try:
        updated = _replace(session, extra=extra_in)
    except TypeError:
        # SimpleNamespace test stub — mutate in place.
        if hasattr(session, "extra") and isinstance(session.extra, dict):
            session.extra["forum_followup_notes"] = notes
            try:
                session_updater(session, now=datetime.now(tz=timezone.utc))
            except Exception:  # noqa: BLE001
                pass
            return
        return
    try:
        session_updater(updated, now=datetime.now(tz=timezone.utc))
    except Exception:  # noqa: BLE001
        pass


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


__all__ = (
    "ForumFollowupResult",
    "RESPONSE_NOTE_RECORDED",
    "SKIPPED_HELPER_SUGGESTED_INTAKE",
    "SKIPPED_NO_RESPONSE_BODY",
    "SKIPPED_NO_SESSION_ANCHOR",
    "detect_followup_directive",
    "handle_forum_followup",
)
