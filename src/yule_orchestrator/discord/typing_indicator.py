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

import contextlib
from typing import Any, AsyncIterator


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


__all__ = ("typing_context", "safe_typing")
