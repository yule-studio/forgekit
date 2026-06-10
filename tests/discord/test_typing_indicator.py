from __future__ import annotations

import asyncio
import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_discord.ui.typing_indicator import safe_typing, typing_context


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeTypingCM:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "_FakeTypingCM":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True


class _FakeChannelWithTyping:
    def __init__(self) -> None:
        self.cm = _FakeTypingCM()
        self.calls = 0

    def typing(self) -> _FakeTypingCM:
        self.calls += 1
        return self.cm


class _FakeChannelNoTyping:
    """Mimics test fakes that don't implement Discord's typing protocol."""

    name = "fake"


class _FakeChannelWithBrokenTyping:
    def typing(self):  # noqa: D401 - intentional simple
        raise RuntimeError("typing endpoint disabled")


class TypingContextTests(unittest.TestCase):
    def test_calls_channel_typing_when_available(self) -> None:
        channel = _FakeChannelWithTyping()

        async def runner() -> None:
            async with typing_context(channel):
                self.assertTrue(channel.cm.entered)
            self.assertTrue(channel.cm.exited)

        _run(runner())
        self.assertEqual(channel.calls, 1)

    def test_no_op_when_channel_has_no_typing(self) -> None:
        channel = _FakeChannelNoTyping()
        called = {"n": 0}

        async def runner() -> None:
            async with typing_context(channel):
                called["n"] += 1

        _run(runner())
        self.assertEqual(called["n"], 1)

    def test_swallows_exceptions_from_typing_helper(self) -> None:
        channel = _FakeChannelWithBrokenTyping()
        called = {"n": 0}

        async def runner() -> None:
            async with typing_context(channel):
                called["n"] += 1

        _run(runner())
        self.assertEqual(called["n"], 1)

    def test_safe_typing_alias_behaves_identically(self) -> None:
        channel = _FakeChannelWithTyping()

        async def runner() -> None:
            async with safe_typing(channel):
                pass

        _run(runner())
        self.assertTrue(channel.cm.entered)
        self.assertTrue(channel.cm.exited)

    def test_block_runs_to_completion_under_typing(self) -> None:
        channel = _FakeChannelWithTyping()
        result = {"value": None}

        async def runner() -> None:
            async with typing_context(channel):
                await asyncio.sleep(0)
                result["value"] = 42

        _run(runner())
        self.assertEqual(result["value"], 42)
        self.assertTrue(channel.cm.exited)


class MemberBotTypingIntegrationTests(unittest.TestCase):
    """The member-bot ``on_message`` path wraps its post helpers in a
    typing context so each bot account shows 입력 중... while the take
    is being delivered. Test only the wrapper composition (typing
    enters before send, exits after) — the outcome dataclass shape
    belongs to engineering_team_runtime tests."""

    def test_post_runs_inside_typing(self) -> None:
        # Mimic the structure of member_bot.on_message: typing first,
        # then channel.send. The integration confirms the typing CM
        # actually wraps the send call.
        from yule_discord.ui.typing_indicator import typing_context

        channel = _FakeChannelWithTyping()
        order: list[str] = []
        async def fake_send(content: str) -> None:
            order.append(f"send:{content}")
        channel.send = fake_send  # type: ignore[attr-defined]

        async def runner() -> None:
            async with typing_context(channel):
                order.append("inside-typing")
                await channel.send("a member take")

        _run(runner())
        self.assertEqual(
            order,
            ["inside-typing", "send:a member take"],
        )
        self.assertTrue(channel.cm.entered)
        self.assertTrue(channel.cm.exited)

    def test_tech_lead_synthesis_post_runs_inside_typing(self) -> None:
        """The tech-lead synthesis comment (RESEARCH_SYNTHESIS_ROLE)
        flows through the same member_bot.on_message → typing_context →
        _post_research_turn path as every other role. We verify the
        wrap holds for the tech-lead-flavoured payload too so the bot
        account shows 입력 중... while synthesis is delivered."""

        from yule_discord.ui.typing_indicator import typing_context

        channel = _FakeChannelWithTyping()
        events: list[str] = []

        async def fake_send(content: str) -> None:
            events.append(f"send:{content[:30]}")
        channel.send = fake_send  # type: ignore[attr-defined]

        synthesis_body = (
            "[Decision] 합의안 — Forum starter 캡 + thread 분할 게시로 "
            "4000자 한도 안정화"
        )

        async def runner() -> None:
            async with typing_context(channel):
                events.append("typing-active")
                await channel.send(synthesis_body)

        _run(runner())
        self.assertTrue(channel.cm.entered)
        self.assertTrue(channel.cm.exited)
        self.assertEqual(events[0], "typing-active")
        self.assertTrue(events[1].startswith("send:"))


if __name__ == "__main__":
    unittest.main()
