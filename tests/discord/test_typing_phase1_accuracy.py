"""Phase 1 stabilisation — typing fires only when a response is
committed; long-running ops keep the indicator alive via heartbeat.

Pin the live-bug regression:

  • The gateway used to wrap the entire engineering route in
    ``typing_context`` so the user saw "입력 중..." even on messages the
    router silently dropped (non-engineering channel, ignored phrase).
    The fix wraps ``send_chunks`` instead — typing fires *only* in the
    moment a chunk is being committed.
  • Discord's native typing event auto-expires after ~10s so research
    loops (multi-second collection + forum publish) used to fade to
    silence mid-flow. ``typing_keepalive`` re-arms the indicator on a
    background task until the wrapped block exits.
"""

from __future__ import annotations

import asyncio
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.discord.ui.typing_indicator import (
    typing_keepalive,
    wrap_send_chunks_with_typing,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _CountingTypingCM:
    """Async context manager that records how many times it's entered.

    Each call to ``channel.typing()`` returns a fresh CM, so counting
    the entries is the same as counting how many distinct typing
    events Discord would have received from the bot.
    """

    def __init__(self, channel: "_CountingChannel") -> None:
        self._channel = channel

    async def __aenter__(self) -> "_CountingTypingCM":
        self._channel.enters += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._channel.exits += 1


class _CountingChannel:
    def __init__(self) -> None:
        self.enters = 0
        self.exits = 0
        self.sends: list[str] = []

    def typing(self) -> _CountingTypingCM:
        return _CountingTypingCM(self)


class _NoTypingChannel:
    """Channel without a ``.typing()`` attribute — graceful no-op path."""

    def __init__(self) -> None:
        self.sends: list[str] = []


class WrapSendChunksWithTypingTests(unittest.TestCase):
    """Wrapping ``send_chunks`` makes typing fire exactly when a chunk
    is committed, and stay silent otherwise.

    Why this matters: the gateway's ``on_message`` previously typed
    unconditionally before deciding whether to respond. Users learned
    to ignore the indicator because it appeared even on no-op
    messages. The fix lets ``send_chunks`` itself open the typing
    context — so typing == real response.
    """

    def test_typing_fires_per_chunk_send(self) -> None:
        channel = _CountingChannel()

        async def base_send(ch, content: str) -> None:
            ch.sends.append(content)

        wrapped = wrap_send_chunks_with_typing(base_send)
        _run(wrapped(channel, "hello"))
        _run(wrapped(channel, "world"))

        # Typing must have entered + exited once per real send.
        self.assertEqual(channel.enters, 2)
        self.assertEqual(channel.exits, 2)
        # Both chunks made it through the wrapper.
        self.assertEqual(channel.sends, ["hello", "world"])

    def test_empty_content_skips_typing(self) -> None:
        channel = _CountingChannel()

        async def base_send(ch, content: str) -> None:
            ch.sends.append(content)

        wrapped = wrap_send_chunks_with_typing(base_send)
        _run(wrapped(channel, ""))
        _run(wrapped(channel, "   "))

        # No typing for whitespace-only sends — those usually mean a
        # caller defensively passed an empty string for an unset hook.
        self.assertEqual(channel.enters, 0)

    def test_no_typing_channel_is_graceful(self) -> None:
        channel = _NoTypingChannel()

        async def base_send(ch, content: str) -> None:
            ch.sends.append(content)

        wrapped = wrap_send_chunks_with_typing(base_send)
        _run(wrapped(channel, "hi"))
        # Should still send even though channel doesn't expose typing.
        self.assertEqual(channel.sends, ["hi"])

    def test_extra_args_pass_through(self) -> None:
        channel = _CountingChannel()
        seen: dict = {}

        async def base_send(ch, content: str, *args, **kwargs) -> None:
            seen["args"] = args
            seen["kwargs"] = kwargs
            ch.sends.append(content)

        wrapped = wrap_send_chunks_with_typing(base_send)
        _run(wrapped(channel, "hi", "extra", flag=True))
        self.assertEqual(seen["args"], ("extra",))
        self.assertEqual(seen["kwargs"], {"flag": True})


class TypingKeepaliveTests(unittest.TestCase):
    """The keepalive re-arms typing every interval seconds so multi-
    second research loops never lose the "bot is composing" cue.

    We use a very short interval (0.05s) so the test stays fast while
    still proving multiple re-fires happen during the wrapped block.
    """

    def test_keepalive_refires_typing_until_block_exits(self) -> None:
        channel = _CountingChannel()

        async def long_running() -> None:
            async with typing_keepalive(channel, interval=0.05):
                # Sleep long enough for at least two refresh cycles.
                await asyncio.sleep(0.18)

        _run(long_running())
        # First fire is immediate, then ≥2 refreshes during the 0.18s
        # block. Lower-bound assertion keeps the test stable on slow
        # CI without being so loose it misses a regression.
        self.assertGreaterEqual(channel.enters, 2)
        self.assertEqual(channel.enters, channel.exits)

    def test_keepalive_releases_typing_on_exit(self) -> None:
        channel = _CountingChannel()

        async def quick() -> None:
            async with typing_keepalive(channel, interval=0.05):
                pass

        _run(quick())
        # Even on immediate exit, the first ``__aenter__`` fired and
        # was released. exits must match enters so we never leak a
        # half-open typing indicator after the block.
        self.assertEqual(channel.enters, channel.exits)

    def test_keepalive_propagates_inner_exception(self) -> None:
        channel = _CountingChannel()

        async def boom() -> None:
            async with typing_keepalive(channel, interval=0.05):
                raise RuntimeError("inner failed")

        with self.assertRaises(RuntimeError):
            _run(boom())
        # Even when the wrapped block raises, the keepalive must clean
        # up its background task and release any in-flight typing CM
        # so we don't leak coroutines or leave the user staring at a
        # stuck indicator.
        self.assertEqual(channel.enters, channel.exits)

    def test_keepalive_no_typing_channel_is_graceful(self) -> None:
        channel = _NoTypingChannel()

        async def quick() -> None:
            async with typing_keepalive(channel, interval=0.05):
                pass

        # Must not raise even when the channel doesn't implement
        # Discord's typing protocol (test fakes, threads without the
        # messageable mixin proxy, etc.).
        _run(quick())


if __name__ == "__main__":
    unittest.main()
