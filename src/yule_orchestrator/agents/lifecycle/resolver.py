"""Engineering session resolve — single source of truth.

Stabilisation Phase 4 surfaced four different code paths that each
resolved "which session does this message belong to?" in slightly
different ways:

  - status diagnostic (``bot._load_latest_open_session_for_status``)
  - Obsidian save approval (router ``_run_obsidian_approval_gate``)
  - clarification follow-up (router clarification cache)
  - coding approval (router coding gate)

When the rules drift apart you get the live-MVP regression where
"세션 abc 기준으로 저장해줘" silently routes to a different session.
This module consolidates the resolve precedence + the friendly error
shape so callers pull the same logic.

Pure read-only — all writes (intake, thread linkage) stay on the
caller. The resolver itself never mutates ``session.extra``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


__all__ = (
    "SessionResolveResult",
    "RESOLVE_OK",
    "RESOLVE_NOT_FOUND",
    "RESOLVE_AMBIGUOUS",
    "RESOLVE_UNAVAILABLE",
    "extract_explicit_session_id",
    "resolve_session_for_message",
)


# Status codes for the resolve result. Callers branch on these
# instead of inspecting ``session is None``.
RESOLVE_OK: str = "resolved"
RESOLVE_NOT_FOUND: str = "not_found"
RESOLVE_AMBIGUOUS: str = "ambiguous"
RESOLVE_UNAVAILABLE: str = "unavailable"


# Same regex as router._EXPLICIT_SESSION_ID_RE / bot._SESSION_ID_PATTERN
# — kept under a third location intentionally so the agents package
# doesn't have to import discord/bot symbols. The pattern is the
# canonical one and the router/bot helpers will be slimmed to call
# this one in a follow-up phase.
_EXPLICIT_SESSION_ID_RE = re.compile(
    r"(?:세션|session)\s*(?:id\s*[:=]?\s*)?[`'\"]?([0-9a-fA-F]{12})[`'\"]?",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class SessionResolveResult:
    """Outcome of :func:`resolve_session_for_message`.

    The ``status`` field tells the caller why ``session`` may be
    ``None``:

      • ``RESOLVE_OK`` — ``session`` is set, ``session_id`` populated.
      • ``RESOLVE_NOT_FOUND`` — explicit id was given but no row
        matched. Caller should reply with "세션 X 을 찾지 못했어요".
      • ``RESOLVE_AMBIGUOUS`` — multiple candidates passed the rules;
        ``candidates`` is non-empty. Caller should ask for
        clarification.
      • ``RESOLVE_UNAVAILABLE`` — list_sessions_fn raised or returned
        nothing usable. Treat as legacy "no open session".

    ``reason`` is a short Korean phrase suitable for surfacing in the
    Discord reply. ``why`` is for log lines (English).
    """

    session: Optional[Any]
    session_id: Optional[str]
    status: str
    reason: Optional[str] = None
    why: Optional[str] = None
    candidates: Tuple[Any, ...] = ()


def extract_explicit_session_id(text: str) -> Optional[str]:
    """Pull a 12-hex session id out of a message that explicitly
    references ``세션 <id>`` or ``session <id>``. Returns the id in
    lowercase, or ``None`` when no match is found.
    """

    if not text:
        return None
    match = _EXPLICIT_SESSION_ID_RE.search(text)
    if match is None:
        return None
    return match.group(1).lower()


def _try_load_session_by_id(
    session_id: str,
    *,
    session_loader: Optional[Callable[[str], Optional[Any]]] = None,
) -> Optional[Any]:
    if not session_id:
        return None
    loader = session_loader
    if loader is None:
        try:
            from ..workflow_state import load_session as _load_session

            loader = _load_session
        except Exception:  # noqa: BLE001
            return None
    try:
        return loader(session_id)
    except Exception:  # noqa: BLE001
        return None


def _state_value(session: Any) -> str:
    state = getattr(session, "state", None)
    return str(getattr(state, "value", state) or "").strip().lower()


def _is_open_state(session: Any) -> bool:
    return _state_value(session) not in ("completed", "rejected")


def _list_open_sessions(
    list_sessions_fn: Optional[Callable[..., Sequence[Any]]],
    *,
    limit: int = 50,
) -> Tuple[Any, ...]:
    if list_sessions_fn is None:
        return ()
    try:
        try:
            sessions = list_sessions_fn(limit=limit)
        except TypeError:
            sessions = list_sessions_fn()
    except Exception:  # noqa: BLE001
        return ()
    return tuple(s for s in (sessions or ()) if _is_open_state(s))


def resolve_session_for_message(
    *,
    message: Any,
    text: str,
    list_sessions_fn: Optional[Callable[..., Sequence[Any]]] = None,
    author_id: Optional[int] = None,
    session_loader: Optional[Callable[[str], Optional[Any]]] = None,
) -> SessionResolveResult:
    """Resolve which session a Discord *message* targets.

    Resolution order:

      1. Explicit ``세션 <id>`` / ``session <id>`` in *text* —
         loaded via *session_loader* (defaults to
         :func:`workflow_state.load_session`).
      2. Current channel/thread id matches ``session.thread_id``.
      3. Current channel id matches
         ``session.extra['research_forum_thread_id']``.
      4. Latest open session for the same channel/user pair (legacy
         fallback).

    *list_sessions_fn* is the open-session iterator; the router
    already wires this to ``workflow_state.list_sessions``. Tests
    that don't need fallback can pass ``None`` and rely on the
    explicit-id branch only.
    """

    explicit_id = extract_explicit_session_id(text)
    if explicit_id:
        candidate = _try_load_session_by_id(
            explicit_id, session_loader=session_loader
        )
        if candidate is not None:
            return SessionResolveResult(
                session=candidate,
                session_id=str(getattr(candidate, "session_id", explicit_id)),
                status=RESOLVE_OK,
                why=f"explicit session id {explicit_id}",
            )
        return SessionResolveResult(
            session=None,
            session_id=explicit_id,
            status=RESOLVE_NOT_FOUND,
            reason=(
                f"세션 `{explicit_id}` 을 찾지 못했어요. "
                "session id 가 정확한지 확인해 주세요."
            ),
            why="explicit id had no matching row",
        )

    channel = getattr(message, "channel", None)
    channel_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    parent = getattr(channel, "parent", None)
    is_thread = parent_id is not None or parent is not None
    msg_thread_id = channel_id if is_thread else None

    sessions = _list_open_sessions(list_sessions_fn)
    if not sessions:
        return SessionResolveResult(
            session=None,
            session_id=None,
            status=RESOLVE_UNAVAILABLE,
            why="no open sessions or list_sessions_fn unavailable",
        )

    # 2. thread anchor — strongest signal when present
    if msg_thread_id is not None:
        thread_matches = tuple(
            s for s in sessions if getattr(s, "thread_id", None) == msg_thread_id
        )
        if len(thread_matches) == 1:
            picked = thread_matches[0]
            return SessionResolveResult(
                session=picked,
                session_id=str(getattr(picked, "session_id", "") or ""),
                status=RESOLVE_OK,
                why="thread_id anchor",
            )
        if len(thread_matches) > 1:
            return SessionResolveResult(
                session=None,
                session_id=None,
                status=RESOLVE_AMBIGUOUS,
                reason=(
                    "이 thread 에 매칭되는 세션이 여러 개라 어느 작업 기준인지 "
                    "확인이 필요해요. `세션 <id>` 처럼 답해 주세요."
                ),
                candidates=thread_matches,
                why="thread anchor ambiguous",
            )

    # 3. forum thread id (research forum)
    if msg_thread_id is not None:
        forum_matches = tuple(
            s
            for s in sessions
            if _extra_int(s, "research_forum_thread_id") == msg_thread_id
            or _extra_int(s, "forum_thread_id") == msg_thread_id
        )
        if len(forum_matches) == 1:
            picked = forum_matches[0]
            return SessionResolveResult(
                session=picked,
                session_id=str(getattr(picked, "session_id", "") or ""),
                status=RESOLVE_OK,
                why="forum thread anchor",
            )
        if len(forum_matches) > 1:
            return SessionResolveResult(
                session=None,
                session_id=None,
                status=RESOLVE_AMBIGUOUS,
                reason=(
                    "이 forum thread 에 매칭되는 세션이 여러 개라 어느 작업 "
                    "기준인지 확인이 필요해요. `세션 <id>` 처럼 답해 주세요."
                ),
                candidates=forum_matches,
                why="forum thread anchor ambiguous",
            )

    # 4. channel + user fallback — last open session for the pair.
    channel_matches: list[Any] = []
    for s in sessions:
        s_channel = getattr(s, "channel_id", None)
        s_user = getattr(s, "user_id", None)
        if channel_id is None and s_channel is None:
            continue
        if channel_id is not None and s_channel != channel_id:
            continue
        if author_id is not None and s_user is not None and s_user != author_id:
            continue
        channel_matches.append(s)
    if not channel_matches:
        return SessionResolveResult(
            session=None,
            session_id=None,
            status=RESOLVE_NOT_FOUND,
            reason=(
                "현재 채널에 매칭되는 열린 세션이 보이지 않아요. "
                "세션 id 를 알려주시거나 작업 thread 안에서 다시 말씀해 주세요."
            ),
            why="no channel/user fallback match",
        )
    if len(channel_matches) == 1:
        picked = channel_matches[0]
        return SessionResolveResult(
            session=picked,
            session_id=str(getattr(picked, "session_id", "") or ""),
            status=RESOLVE_OK,
            why="channel/user fallback (single open)",
        )

    # Multiple — sort by updated_at desc and take the latest as the
    # primary, but report the rest as candidates so the caller can
    # offer a clarification UI.
    def _updated_at(s: Any):
        ts = getattr(s, "updated_at", None)
        try:
            return ts.timestamp() if ts is not None else 0
        except Exception:  # noqa: BLE001
            return 0

    channel_matches.sort(key=_updated_at, reverse=True)
    picked = channel_matches[0]
    others = tuple(channel_matches[1:])
    return SessionResolveResult(
        session=picked,
        session_id=str(getattr(picked, "session_id", "") or ""),
        status=RESOLVE_OK,
        why=f"channel/user fallback (latest of {len(channel_matches)})",
        candidates=others,
    )


def _extra_int(session: Any, key: str) -> Optional[int]:
    try:
        extra = getattr(session, "extra", None) or {}
        if not isinstance(extra, Mapping):
            return None
        raw = extra.get(key)
        if raw is None:
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None
