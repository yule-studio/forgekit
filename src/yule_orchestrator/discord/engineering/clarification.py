"""Engineering router — clarification cache + follow-up selection.

When the gateway shows the user a candidate clarification (multiple
sessions could match the request), we cache the candidates plus the
canonical task prompt that triggered the question. The next reply
("1번" / "기존 세션으로 진행" / "새 작업으로 진행" / "이걸로") is
resolved against the cache, never re-classified from scratch — that
keeps the routing-command reply itself from becoming a session
prompt or research query.

This module owns three concerns:

  - the in-memory cache mapping ``(channel_or_thread_id, user_id) →
    {candidates, canonical_prompt}``,
  - the recall + clear helpers,
  - the selection helpers (numeric / Korean ordinal / demonstrative
    phrase / explicit "새 작업으로 진행" detection).

Lives outside the giant ``engineering_channel_router`` so the router
file stays focused on flow orchestration. Symbols are re-exported
from the router for backward compatibility — tests and other
callers that import from ``engineering_channel_router`` keep working
without modification.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional, Sequence


__all__ = (
    "CLARIFICATION_CACHE_TTL_SECONDS",
    "GATEWAY_CLARIFICATION_CONTEXT",
    "clarification_context_key",
    "remember_clarification_candidates",
    "recall_clarification_candidates",
    "recall_clarification_canonical_prompt",
    "clear_clarification_context",
    "try_select_candidate",
    "looks_like_new_work_selection",
)


# P0-N4 (live bug #5) — clarification cache TTL. Without a TTL the
# cache lives until the next explicit clear, so an abandoned
# clarification turn (user types something unrelated, then returns
# hours later with "새 작업으로 진행") can spawn a session whose
# canonical_prompt is stale. 30 min covers active back-and-forth
# while keeping the canonical attached to the actual conversation.
CLARIFICATION_CACHE_TTL_SECONDS: int = 30 * 60


# Cached value shape:
#   {"candidates": tuple[dict, ...], "canonical_prompt": Optional[str]}
# ``canonical_prompt`` is the original task description that triggered
# the clarification — used on a follow-up "새 작업으로 진행" / "1번" /
# "기존 세션 <id>" reply so the next session/forum/research call gets
# the real task text instead of the routing-command phrase ("새 작업
# 으로 진행" is never a valid session.prompt).
GATEWAY_CLARIFICATION_CONTEXT: dict[
    tuple[Optional[int], Optional[int]], dict
] = {}


_NUMERIC_SELECTION_RE = re.compile(
    r"^\s*(\d{1,2})\s*(번|번째|개|위치)?\s*\.?\s*$"
)


# Map a Korean ordinal/positional prefix to a 1-based candidate index.
# We match by ``startswith`` after whitespace removal so phrases like
# "첫 번째 거" / "두번째로" still resolve.
_ORDINAL_KO_PREFIXES: tuple[tuple[str, int], ...] = (
    ("첫번째", 1),
    ("첫 번째", 1),
    ("첫째", 1),
    ("두번째", 2),
    ("두 번째", 2),
    ("둘째", 2),
    ("세번째", 3),
    ("세 번째", 3),
    ("셋째", 3),
    ("네번째", 4),
    ("네 번째", 4),
    ("넷째", 4),
    ("다섯번째", 5),
    ("다섯 번째", 5),
    ("다섯째", 5),
)


# Phrases that mean "the one I just showed" — only meaningful with at
# least one stored candidate. With multiple candidates these stay
# ambiguous and we ask for a number; with a single candidate they pick
# it. ``기존 세션으로 진행`` is included so users who saw a
# single-candidate clarification can confirm with that exact wording.
_DEMONSTRATIVE_SELECTION_PHRASES: tuple[str, ...] = (
    "이걸로",
    "이거로",
    "이걸루",
    "이거",
    "저걸로",
    "그걸로",
    "위에 거",
    "위에거",
    "위 거",
    "위 것",
    "방금 그거",
    "방금 그것",
    "방금 거",
    "기존 세션으로 진행",
    "기존 작업으로 진행",
)


_NEW_WORK_SELECTION_PHRASES: tuple[str, ...] = (
    "새 작업으로 진행",
    "새 작업으로 등록",
    "새 작업으로 시작",
    "새 작업 만들어",
    "새 세션으로 진행",
    "새 세션으로 등록",
    "새 thread로",
    "새 스레드로",
    "create new work",
    "create a new task",
    "start a new session",
    "new thread",
    "new session",
)


def clarification_context_key(message: Any) -> tuple[Optional[int], Optional[int]]:
    """Scope key for the clarification cache.

    Uses the channel/thread id the user is currently typing in, plus
    the author id, so a clarification shown to user A in #업무-접수
    doesn't get hijacked by user B's "1번" reply in the same channel.
    """

    channel = getattr(message, "channel", None)
    scope_id = getattr(channel, "id", None)
    user_id = getattr(getattr(message, "author", None), "id", None)
    return (scope_id, user_id)


def remember_clarification_candidates(
    message: Any,
    candidates: Sequence[Any],
    *,
    canonical_prompt: Optional[str] = None,
) -> None:
    """Stash candidate session ids + thread ids + the original task prompt.

    ``canonical_prompt`` is the actionable task text that was active
    when the gateway showed the clarification. On a follow-up turn
    ("새 작업으로 진행" / "1번" / "기존 세션 abc") the router pulls
    this back out so session.prompt + research forum body +
    research_loop prompt_text all use the real task instead of the
    routing-command phrase. Caller may pass ``None`` when only
    refreshing the candidate list — in that case any previously-cached
    canonical_prompt is preserved.

    Stored as a plain dict so the cache value round-trips through
    pickling-friendly types and we never hold a reference to a
    dataclass that may grow new fields underneath us.
    """

    if not candidates and canonical_prompt is None:
        return
    serialized = tuple(
        {
            "session_id": getattr(cand, "session_id", None),
            "title": getattr(cand, "title", "") or "",
            "score": float(getattr(cand, "score", 0.0) or 0.0),
            "thread_id": getattr(cand, "thread_id", None),
            "forum_thread_id": getattr(cand, "forum_thread_id", None),
            "task_type": getattr(cand, "task_type", None),
        }
        for cand in (candidates or ())[:5]
        if getattr(cand, "session_id", None)
    )
    key = clarification_context_key(message)
    existing = GATEWAY_CLARIFICATION_CONTEXT.get(key) or {}
    if canonical_prompt is not None:
        cleaned_canonical = canonical_prompt.strip() or None
    else:
        cleaned_canonical = existing.get("canonical_prompt")
    payload: dict = {}
    if serialized:
        payload["candidates"] = serialized
    elif "candidates" in existing:
        payload["candidates"] = existing["candidates"]
    if cleaned_canonical:
        payload["canonical_prompt"] = cleaned_canonical
    if not payload:
        return
    # P0-N4: stamp write time so reads can drop expired entries.
    # Preserve existing created_at if the call only refreshed canonical
    # — the TTL measures clarification age, not last write.
    payload["created_at"] = existing.get("created_at") or time.time()
    GATEWAY_CLARIFICATION_CONTEXT[key] = payload


def _fetch_fresh_cache_entry(key: tuple) -> Optional[Any]:
    """Return cache value at *key* dropping stale entries first.

    Bare tuple values (older test fixtures) bypass the TTL because
    they predate the dict shape. Dict entries with a missing
    ``created_at`` are treated as "just written" so we don't lose
    legitimate in-flight clarifications during the upgrade window.
    """

    cached = GATEWAY_CLARIFICATION_CONTEXT.get(key)
    if cached is None:
        return None
    if isinstance(cached, dict):
        created_at = cached.get("created_at")
        if isinstance(created_at, (int, float)):
            if time.time() - float(created_at) > CLARIFICATION_CACHE_TTL_SECONDS:
                GATEWAY_CLARIFICATION_CONTEXT.pop(key, None)
                return None
    return cached


def recall_clarification_candidates(message: Any) -> tuple[dict, ...]:
    cached = _fetch_fresh_cache_entry(clarification_context_key(message))
    if cached is None:
        return ()
    # Backward-compat: older callers (and a couple of test fixtures)
    # wrote a bare ``tuple[dict, ...]`` directly into the cache. Treat
    # those as candidates-only so the runtime preflight still resolves
    # "1번" / demonstrative selections cleanly.
    if isinstance(cached, tuple):
        return cached
    if isinstance(cached, dict):
        return tuple(cached.get("candidates") or ())
    return ()


def recall_clarification_canonical_prompt(message: Any) -> Optional[str]:
    """Return the actionable task text captured at clarification time.

    Returns ``None`` when no clarification cache exists for this
    channel/user pair, when the cache was populated without a
    canonical_prompt (older entries before the canonical prompt
    handoff fix), or when the cache has aged past
    :data:`CLARIFICATION_CACHE_TTL_SECONDS` (P0-N4 — stale canonical
    on a long-abandoned clarification must never become session.prompt).
    """

    cached = _fetch_fresh_cache_entry(clarification_context_key(message))
    if not isinstance(cached, dict):
        return None
    raw = cached.get("canonical_prompt")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def clear_clarification_context(message: Any) -> None:
    GATEWAY_CLARIFICATION_CONTEXT.pop(clarification_context_key(message), None)


def try_select_candidate(
    text: str,
    candidates: tuple[dict, ...],
) -> Optional[dict]:
    """Resolve a follow-up message into a stored candidate, or None.

    Recognises:

    - bare number ``"1"`` / ordinal-shaped ``"1번"`` / ``"2번째"``
    - Korean ordinals ``"첫 번째"`` / ``"두번째"`` / ...
    - demonstrative phrases (``"이걸로"`` / ``"기존 세션으로 진행"``)
      — only return a hit when there's exactly one stored candidate
      so multi-candidate ambiguity falls through to a fresh
      clarification instead of being silently resolved.

    Out-of-range numbers (e.g. user typed "9번" but only 3 candidates)
    return None so the router can re-ask. The cache is left in place
    because the next reply might still be a valid pick.
    """

    if not candidates:
        return None
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return None

    numeric_match = _NUMERIC_SELECTION_RE.match(cleaned)
    if numeric_match is not None:
        index = int(numeric_match.group(1)) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
        return None

    for prefix, idx in _ORDINAL_KO_PREFIXES:
        if cleaned.startswith(prefix) and idx <= len(candidates):
            return candidates[idx - 1]

    if any(phrase in cleaned for phrase in _DEMONSTRATIVE_SELECTION_PHRASES):
        if len(candidates) == 1:
            return candidates[0]
        return None

    return None


def looks_like_new_work_selection(text: str) -> bool:
    """True when *text* is a routing-command meaning "make a new session".

    Used for the clarification follow-up branch — when the gateway
    just asked "어느 작업에 합류할까요?" and the user replies
    "새 작업으로 진행", we go to CREATE *with the cached canonical
    prompt*, not with the routing-command phrase. Without a cached
    canonical the router refuses (clarification) so this phrase can
    never become session.prompt on its own.
    """

    cleaned = " ".join((text or "").lower().split()).strip(" .!?")
    if not cleaned:
        return False
    return any(phrase in cleaned for phrase in _NEW_WORK_SELECTION_PHRASES)
