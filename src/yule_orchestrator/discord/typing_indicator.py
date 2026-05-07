"""Async ``async with`` helper that shows ``BotName is typing...`` in Discord.

The gateway, tech-lead, and member bots all run long awaits (auto
collection, deliberation, forum publishing) where the user otherwise
sees nothing happen for several seconds. Wrapping those awaits in
``async with channel.typing():`` makes Discord display the bot's own
account as typing, so it feels like a real teammate is composing a
reply.

Real ``discord.abc.Messageable`` channels expose ``.typing()`` returning
an async context manager. Test fakes don't, and we don't want
``AttributeError`` to derail an actual operation. :func:`typing_context`
returns a graceful no-op when the channel can't type; :func:`safe_typing`
is the alias used in module bodies that read better with the verb.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence


@contextlib.asynccontextmanager
async def typing_context(channel: Any) -> AsyncIterator[None]:
    """Show the calling bot as typing in *channel* for the wrapped block.

    No-ops when *channel* has no callable ``typing`` attribute (test
    fakes, threads that didn't proxy the messageable mixin, etc.). Any
    exception raised by ``channel.typing()`` itself is swallowed and we
    fall through without typing — the work the caller is doing must not
    fail just because the indicator can't be shown.
    """

    typer = getattr(channel, "typing", None)
    if not callable(typer):
        yield
        return

    try:
        ctx = typer()
    except Exception:  # noqa: BLE001 - never let typing break the work
        yield
        return

    aenter = getattr(ctx, "__aenter__", None)
    aexit = getattr(ctx, "__aexit__", None)
    if not callable(aenter) or not callable(aexit):
        yield
        return

    try:
        await aenter()
    except Exception:  # noqa: BLE001 - graceful fallback
        yield
        return
    try:
        yield
    finally:
        try:
            await aexit(None, None, None)
        except Exception:  # noqa: BLE001 - best effort
            pass


# Alias so callers can write ``async with safe_typing(channel):`` when
# that reads better than ``typing_context``.
safe_typing = typing_context


# ---------------------------------------------------------------------------
# Phase 5 stabilisation — typing guards
# ---------------------------------------------------------------------------
#
# typing 은 "곧 응답을 보낸다" 신호여야 한다. on_message 진입 직후
# 켜는 것이 아니라 응답 branch 가 확정된 다음에만 켜야 사용자가
# typing 을 관측 신호로 신뢰할 수 있다. 아래 helper 는 그 결정을
# pure 함수로 만들어 gateway / member bot 두 곳에서 동일한 정책을
# 적용한다.


def should_type_for_member_research(
    *,
    role: str,
    active_roles,
    will_post: bool,
) -> bool:
    """Decide whether a member bot should turn its typing indicator on
    for a research-open / team-turn message.

    The active-role gate is implemented in
    :func:`agents.engineering_team_runtime.deliberation_research_role_sequence`
    via the outcome-is-None contract — but having an explicit
    predicate makes the intent visible at the call site (member_bot
    on_message) and lets tests pin the rule directly.

    *will_post* is the resolved "did the handler return a non-None
    outcome?" boolean. typing must NEVER fire when ``will_post`` is
    False.
    """

    if not will_post:
        return False
    if not role:
        return False
    if not active_roles:
        # Legacy session without role_selection metadata — fall back
        # to the default-active behaviour the gateway used before
        # Phase 5. The handler outcome guard above is still authoritative.
        return True
    role_set = {str(r).strip() for r in active_roles if isinstance(r, str)}
    return role.strip() in role_set


def should_type_for_gateway_action(
    *,
    is_engineering_channel: bool,
    handled_branch_likely: bool,
) -> bool:
    """Decide whether the gateway should turn typing on for a message.

    *handled_branch_likely* covers the early checks the gateway
    already runs (non-empty, non-bot, non-slash, in guild). When
    those gates pass we type — otherwise we stay quiet so the user
    knows ignored messages were ignored.
    """

    return bool(is_engineering_channel) and bool(handled_branch_likely)


# ---------------------------------------------------------------------------
# Phase 1 stabilisation — typing accuracy + heartbeat + send-chunks wrap
# ---------------------------------------------------------------------------
#
# Live regression: the gateway opens ``typing_context`` *before* the
# route decides whether to respond, so users see "입력 중..." even on
# messages the bot will silently ignore. And on multi-second flows
# (research loop, synthesis) Discord's native typing fades after ~10s
# leaving long silent gaps. Two helpers below fix both:
#
#   • ``wrap_send_chunks_with_typing`` — wraps any ``send_chunks(channel,
#     text, ...)`` callable so typing fires *only* in the moment a chunk
#     is being committed to the user. Lets the gateway drop its outer
#     unconditional ``typing_context`` wrapper without losing the
#     "bot is composing" cue on real responses.
#
#   • ``typing_keepalive`` — async context manager that re-arms typing
#     every *interval* seconds for the duration of the wrapped block.
#     Use it around long awaits (research collection, deliberation,
#     work-report build) where the user otherwise sees nothing happen
#     for tens of seconds.


@contextlib.asynccontextmanager
async def typing_keepalive(
    channel: Any,
    *,
    interval: float = 8.0,
    label: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AsyncIterator[None]:
    """Hold a typing indicator on *channel* for the duration of the block.

    Discord's typing event auto-expires after ~10s; for operations that
    take longer we re-fire ``channel.typing()`` on a background task
    every *interval* seconds. The first ``__aenter__`` fires before
    yielding so callers see typing immediately. ``label`` and
    ``session_id`` are accepted (and ignored here) so future activity-log
    wiring (Phase 5) can attribute the heartbeat to a specific step
    without changing the call sites.

    Falls back to a graceful no-op if the channel can't type — same
    contract as :func:`typing_context`.
    """

    typer = getattr(channel, "typing", None)
    if not callable(typer):
        yield
        return

    stop_event = asyncio.Event()

    async def _refresh_loop() -> None:
        # First iteration mirrors the initial typing_context the caller
        # would otherwise have wrapped. Subsequent iterations re-arm
        # the indicator before Discord's auto-expiry.
        while not stop_event.is_set():
            try:
                ctx = typer()
                aenter = getattr(ctx, "__aenter__", None)
                aexit = getattr(ctx, "__aexit__", None)
                if callable(aenter) and callable(aexit):
                    try:
                        await aenter()
                    except Exception:  # noqa: BLE001 - never break the work
                        pass
                    else:
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(), timeout=interval
                            )
                        except asyncio.TimeoutError:
                            pass
                        try:
                            await aexit(None, None, None)
                        except Exception:  # noqa: BLE001 - best effort
                            pass
                        continue
            except Exception:  # noqa: BLE001 - never let typing break work
                pass
            # Channel doesn't expose a usable async context manager;
            # wait one interval and retry — saves us from busy-looping
            # if the channel was momentarily unavailable.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        stop_event.set()
        try:
            await task
        except Exception:  # noqa: BLE001 - best effort
            pass


def wrap_send_chunks_with_typing(
    send_chunks: Callable[..., Awaitable[Any]],
    *,
    skip_empty: bool = True,
) -> Callable[..., Awaitable[Any]]:
    """Return a SendChunksFn that types right before each chunk send.

    The wrapped function preserves the original signature
    ``(channel, content, *args, **kwargs)`` so it slots into the
    router's ``SendChunksFn`` contract without ceremony. Each call
    enters ``typing_context(channel)`` for the actual send, then exits.
    That means the gateway only shows "입력 중..." while a real response
    is being committed — never on messages the route will silently drop.

    *skip_empty* short-circuits the typing wrapper when the content is
    empty/whitespace-only so we don't fire "입력 중..." on a no-op send.
    """

    async def _send(channel: Any, content: Any = "", *args: Any, **kwargs: Any) -> Any:
        if skip_empty and isinstance(content, str) and not content.strip():
            return await send_chunks(channel, content, *args, **kwargs)
        async with typing_context(channel):
            return await send_chunks(channel, content, *args, **kwargs)

    return _send


__all__ = (
    "typing_context",
    "typing_keepalive",
    "wrap_send_chunks_with_typing",
    "safe_typing",
    "should_type_for_gateway_action",
    "should_type_for_member_research",
)
