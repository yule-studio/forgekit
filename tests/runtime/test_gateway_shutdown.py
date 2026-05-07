"""SIGTERM-aware gateway runner — A-M6.2 unit tests.

Pin the contract introduced by
:func:`run_engineering_gateway_until_shutdown`: a fake bot whose
``start`` blocks until ``close`` is called must observe
``close()`` exactly once when the shutdown event fires.

We avoid loading ``discord.py`` here — the runner imports
``discord.LoginFailure`` lazily, so a stub module on
``sys.modules`` is enough for unit tests.
"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from typing import Any, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


def _ensure_fake_discord_module() -> None:
    """Provide a tiny ``discord`` module if discord.py isn't installed.

    The runner only touches :class:`discord.LoginFailure` from the
    real package; a stub class is enough for the shutdown path.
    Tests that need real discord.py-specific behaviour live elsewhere.
    """

    if "discord" in sys.modules:
        return
    fake = types.ModuleType("discord")

    class _LoginFailure(Exception):
        pass

    fake.LoginFailure = _LoginFailure  # type: ignore[attr-defined]
    sys.modules["discord"] = fake


_ensure_fake_discord_module()

from yule_orchestrator.discord.bot import (  # noqa: E402
    run_engineering_gateway_until_shutdown,
)


class _FakeBot:
    """Minimal discord.py-shaped bot for the runner contract."""

    def __init__(self) -> None:
        self.start_calls: List[str] = []
        self.close_calls: int = 0
        self._stopped = asyncio.Event()

    async def start(self, token: str) -> None:
        self.start_calls.append(token)
        await self._stopped.wait()

    async def close(self) -> None:
        self.close_calls += 1
        self._stopped.set()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class GatewayShutdownTests(unittest.TestCase):
    def test_shutdown_event_triggers_close_and_runner_returns(self) -> None:
        async def driver() -> _FakeBot:
            shutdown = asyncio.Event()
            bot = _FakeBot()

            runner_task = asyncio.create_task(
                run_engineering_gateway_until_shutdown(
                    shutdown_event=shutdown,
                    bot_factory=lambda: bot,
                    token="tok",
                )
            )
            # Yield once so the runner reaches ``await bot.start``.
            for _ in range(3):
                await asyncio.sleep(0)
            shutdown.set()
            await runner_task
            return bot

        bot = _run(driver())
        self.assertEqual(bot.start_calls, ["tok"])
        self.assertEqual(bot.close_calls, 1)

    def test_login_failure_translates_to_value_error(self) -> None:
        import discord

        class _BrokenBot(_FakeBot):
            async def start(self, token: str) -> None:  # type: ignore[override]
                raise discord.LoginFailure("invalid token")

        async def driver() -> None:
            shutdown = asyncio.Event()
            await run_engineering_gateway_until_shutdown(
                shutdown_event=shutdown,
                bot_factory=_BrokenBot,
                token="tok",
            )

        with self.assertRaises(ValueError) as ctx:
            _run(driver())
        # The wrapped error tells the operator to check env, not
        # leak token state.
        self.assertIn("DISCORD_BOT_TOKEN", str(ctx.exception))

    def test_close_exception_is_swallowed(self) -> None:
        # A graceful close() failure must NOT abort the runner —
        # the bot's start() task is still owned by us; we still
        # need to await it to avoid leaving a pending task.
        class _CloseFailsBot(_FakeBot):
            async def close(self) -> None:  # type: ignore[override]
                self.close_calls += 1
                self._stopped.set()
                raise RuntimeError("WS already closed")

        async def driver() -> _CloseFailsBot:
            shutdown = asyncio.Event()
            bot = _CloseFailsBot()
            task = asyncio.create_task(
                run_engineering_gateway_until_shutdown(
                    shutdown_event=shutdown,
                    bot_factory=lambda: bot,
                    token="tok",
                )
            )
            for _ in range(3):
                await asyncio.sleep(0)
            shutdown.set()
            await task
            return bot

        bot = _run(driver())
        self.assertEqual(bot.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
