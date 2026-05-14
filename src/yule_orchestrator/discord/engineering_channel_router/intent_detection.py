"""engineering_channel_router — channel + confirmation + continuation signals.

Three predicates the main route consults before any orchestration:

- :func:`is_engineering_channel` — message location check against the
  configured intake channel (id or name, including parent-thread match).
- :func:`detect_confirmation_signal` — keyword-based "go ahead" detector
  used when the conversation layer didn't pre-classify ``confirmed``.
- :func:`should_continue_existing_thread` /
  :func:`should_start_new_thread` — keyword pairs that read explicit
  "이어가 / 새 작업으로 진행" overrides in the user's last turn.

``_CONFIRMATION_KEYWORDS`` is the single-source lexicon; the
conversation layer's intent classifier owns its own broader phrase set
(intent_detection.py inside ``engineering_conversation``).
"""

from __future__ import annotations

from typing import Any

from .models import EngineeringRouteContext
from .utils import _normalize_channel_name


# Single-source confirmation lexicon; the engineering conversation layer
# may also detect intent and pre-set ``confirmed=True`` itself, in which
# case the router trusts that signal.
_CONFIRMATION_KEYWORDS: tuple[str, ...] = (
    "확정",
    "진행",
    "시작해",
    "시작하자",
    "시작할게",
    "시작합시다",
    "고고",
    "ㄱㄱ",
    "ㄱㄱㄱ",
    "맞아 진행",
    "그대로 진행",
    "그대로 가",
    "오케이 진행",
    "오케 진행",
    "go ahead",
    "let's go",
    "lets go",
    "kick off",
    "kickoff",
    "proceed",
    "approve and start",
)


def is_engineering_channel(
    *,
    message: Any,
    route_context: EngineeringRouteContext,
) -> bool:
    if not route_context.configured:
        return False

    channel = getattr(message, "channel", None)
    if channel is None:
        return False

    channel_id = getattr(channel, "id", None)
    parent = getattr(channel, "parent", None)
    parent_id = getattr(parent, "id", None) or getattr(channel, "parent_id", None)
    channel_name = _normalize_channel_name(getattr(channel, "name", None))
    parent_name = _normalize_channel_name(getattr(parent, "name", None))

    target_id = route_context.intake_channel_id
    target_name = _normalize_channel_name(route_context.intake_channel_name)

    if target_id is not None:
        if channel_id is not None and channel_id == target_id:
            return True
        if parent_id is not None and parent_id == target_id:
            return True
    if target_name:
        if channel_name == target_name:
            return True
        if parent_name == target_name:
            return True
    return False


def detect_confirmation_signal(text: str) -> bool:
    """Heuristic confirmation detector used when the conversation layer
    does not pre-classify intent.  Matches Korean and English go-ahead
    phrases conservatively — short ack words like ``yes``/``네`` are
    excluded so casual chat isn't promoted to a workflow intake."""

    if not text:
        return False
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _CONFIRMATION_KEYWORDS)


def should_continue_existing_thread(*texts: str) -> bool:
    """True when the user asked to reuse an existing workflow thread/session."""

    normalized = " ".join(
        " ".join(str(text or "").lower().split()) for text in texts
    )
    if not normalized.strip():
        return False
    continuation_signals = (
        "새로 등록하지 말고",
        "새로 만들지 말고",
        "새 스레드 만들지",
        "새 thread 만들지",
        "새로운 스레드",
        "새 thread",
        "기존 스레드",
        "기존 thread",
        "열려 있는 스레드",
        "열려있는 스레드",
        "열려 있는 thread",
        "열려있는 thread",
        "이어가",
        "이어 가",
        "이어서",
        "continue existing",
        "reuse thread",
        "same thread",
        "do not create a new thread",
        "don't create a new thread",
    )
    return any(signal in normalized for signal in continuation_signals)


def should_start_new_thread(text: str) -> bool:
    """True when the latest user turn explicitly overrides continuation."""

    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return False
    force_new_signals = (
        "새 작업으로 진행",
        "새 작업으로 시작",
        "새로 등록해",
        "새로 등록",
        "새 스레드로",
        "새 thread로",
        "새 세션으로",
        "new thread",
        "new session",
    )
    return any(signal in normalized for signal in force_new_signals)


__all__ = (
    "_CONFIRMATION_KEYWORDS",
    "detect_confirmation_signal",
    "is_engineering_channel",
    "should_continue_existing_thread",
    "should_start_new_thread",
)
