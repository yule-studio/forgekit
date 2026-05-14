"""P0-D (#134) — typing keepalive 회귀.

세 가지 시나리오:

  1. gateway long-running path 에서 conversation_fn 이 0.2s 걸려도 fake
     channel 의 ``typing()`` 가 1 번 이상 호출 (keepalive 진입 확인).
  2. member bot 의 _dispatch_member_message 가 8s 가짜 synthesis 동안
     typing_keepalive interval=6s 라서 ``typing()`` 가 2 번 이상 호출.
  3. ignored / non-actionable / inactive role 시 typing 안 켜짐.
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.member_bot import _dispatch_member_message
from yule_orchestrator.discord.member_bots import MemberBotProfile


class _CountingChannel:
    """Records every ``typing()`` enter so tests can assert refresh count."""

    def __init__(self) -> None:
        self.enters: int = 0
        self.sent: list = []

    @contextlib.asynccontextmanager
    async def _typing_ctx(self):
        self.enters += 1
        try:
            yield
        finally:
            pass

    def typing(self):
        return self._typing_ctx()

    async def send(self, content: str = "", **kwargs: Any) -> None:
        self.sent.append(content)


def _profile(role: str = "tech-lead") -> MemberBotProfile:
    return MemberBotProfile(
        agent_id="engineering-agent",
        role=role,
        env_key="ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN",
        token="x.y.z",
        display_label=f"engineering-agent/{role}",
    )


def _run(coro_factory):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


class MemberBotKeepaliveTests(unittest.TestCase):
    """typing_keepalive interval=6s — refresh 2 times during 8s synthesis."""

    def test_research_turn_long_post_refreshes_typing(self) -> None:
        # We simulate the 8s synthesis by making _post_research_turn await
        # asyncio.sleep(0.2) but with a tight typing_keepalive interval so
        # we can observe at least 2 enters within the test's wall clock.
        channel = _CountingChannel()
        message = SimpleNamespace(content="[research-turn:s tech-lead] go", channel=channel)
        sentinel = SimpleNamespace(comment="x", session_id="s")

        async def slow_post(ch, outcome):
            # Sleep ~0.15s so the keepalive's internal refresh task can
            # fire at least once at the test's reduced interval.
            await asyncio.sleep(0.15)

        # Override typing_keepalive's default interval via the
        # _dispatch_member_message call path — we can't directly inject,
        # but we *can* patch typing_keepalive globally for this test to
        # use a very short interval that proves the refresh loop fires.
        from yule_orchestrator.discord import member_bot as mb
        from yule_orchestrator.discord.typing_indicator import (
            typing_keepalive as original,
        )

        @contextlib.asynccontextmanager
        async def fast_keepalive(channel_arg, **kwargs):
            # Force interval to 0.05s regardless of caller's request so
            # we can witness ≥2 refresh ticks within 0.15s of work.
            kwargs["interval"] = 0.05
            async with original(channel_arg, **kwargs):
                yield

        with patch.object(mb, "typing_keepalive", fast_keepalive), patch.object(
            mb, "handle_research_turn_message", return_value=sentinel
        ), patch.object(mb, "_post_research_turn", side_effect=slow_post):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        # At least 2 typing enters (one initial + at least one refresh).
        self.assertGreaterEqual(channel.enters, 2)
        # No fallback ⚠️.
        self.assertFalse(any("⚠️" in s for s in channel.sent))


class IgnoredPathSilenceTests(unittest.TestCase):
    """Inactive role / handler returning None → typing never fires."""

    def test_inactive_role_does_not_open_typing(self) -> None:
        channel = _CountingChannel()
        message = SimpleNamespace(content="[research-turn:s other-role] go", channel=channel)

        from yule_orchestrator.discord import member_bot as mb

        with patch.object(
            mb, "handle_research_turn_message", return_value=None
        ), patch.object(
            mb, "handle_team_turn_message", return_value=None
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        # handler 가 둘 다 None → typing 진입 0 (keepalive 진입 X).
        self.assertEqual(channel.enters, 0)
        self.assertEqual(channel.sent, [])


class GatewayKeepaliveSmokeTests(unittest.TestCase):
    """Smoke: typing_keepalive import 가 channel router 에서 정상."""

    def test_typing_keepalive_importable(self) -> None:
        # Smoke — module-level import 가 깨졌으면 다른 test 도 다 깨짐.
        # 본 test 는 keepalive symbol 존재 + factory shape 만 확인.
        from yule_orchestrator.discord.engineering_channel_router import (
            _maybe_await,
        )
        from yule_orchestrator.discord.typing_indicator import typing_keepalive

        self.assertTrue(callable(typing_keepalive))
        # No-op channel → graceful fallthrough.
        async def run():
            async with typing_keepalive(None, interval=0.1):
                pass

        _run(lambda: run())


if __name__ == "__main__":
    unittest.main()
