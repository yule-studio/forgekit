"""P0-F commit 5 — member bot typing pre-parse pre-gate.

Before P0-F, ``_dispatch_member_message`` called the expensive
``handle_research_turn_message`` / ``handle_team_turn_message``
handlers *before* entering ``typing_keepalive``. The handlers
load session, run deliberation, and queue synthesis (5-15 s), so
the typing indicator stayed dark for most of the work.

P0-F flips the order: cheap regex pre-parse first (matches
marker + role), then ``typing_keepalive`` enters, then the
expensive handler runs inside the wrap. Ignored / no-marker / wrong-
role messages return *before* the keepalive even starts so typing
never lights up on dropped traffic.

Regression coverage:

  1. no marker → typing never starts (silence contract).
  2. marker matches another role → typing never starts.
  3. matching research-turn marker → typing wrap fires *before*
     the handler is invoked.
  4. matching team-turn marker → same.
  5. research-open marker (role-less) → typing fires for every
     active member bot.
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.member_bot import _dispatch_member_message
from yule_orchestrator.discord.member_bots import MemberBotProfile


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


class _Channel:
    def __init__(self) -> None:
        self.sent: list = []

    @contextlib.asynccontextmanager
    async def _typing(self):
        yield

    def typing(self):
        return self._typing()

    async def send(self, content: str = "", **kwargs: Any) -> None:
        self.sent.append(content)


class PreParseSilenceTests(unittest.TestCase):
    """No marker / mismatched role → handler never called, typing never opened."""

    def test_no_marker_does_not_call_handlers(self) -> None:
        from yule_orchestrator.discord import member_bot as mb

        channel = _Channel()
        message = SimpleNamespace(content="평범한 메시지", channel=channel)

        handler_called: List = []

        def fake_research(**kwargs):
            handler_called.append("research")
            return None

        def fake_team(**kwargs):
            handler_called.append("team")
            return None

        keepalive_calls: List = []

        @contextlib.asynccontextmanager
        async def counting_keepalive(ch, **kwargs):
            keepalive_calls.append(kwargs.get("label"))
            yield

        with patch.object(
            mb, "handle_research_turn_message", side_effect=fake_research
        ), patch.object(
            mb, "handle_team_turn_message", side_effect=fake_team
        ), patch.object(mb, "typing_keepalive", counting_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile(), message=message
                )
            )

        self.assertEqual(handler_called, [])
        self.assertEqual(keepalive_calls, [])
        self.assertEqual(channel.sent, [])

    def test_wrong_role_marker_silent(self) -> None:
        # research-turn marker but role=ai-engineer; this bot is
        # tech-lead → pre-gate False, handler not called.
        from yule_orchestrator.discord import member_bot as mb

        channel = _Channel()
        message = SimpleNamespace(
            content="[research-turn:s ai-engineer] go", channel=channel
        )

        handler_called: List = []

        def fake_research(**kwargs):
            handler_called.append("research")
            return None

        keepalive_calls: List = []

        @contextlib.asynccontextmanager
        async def counting_keepalive(ch, **kwargs):
            keepalive_calls.append(kwargs.get("label"))
            yield

        with patch.object(
            mb, "handle_research_turn_message", side_effect=fake_research
        ), patch.object(
            mb, "handle_team_turn_message", return_value=None
        ), patch.object(mb, "typing_keepalive", counting_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("tech-lead"), message=message
                )
            )

        self.assertEqual(handler_called, [])
        self.assertEqual(keepalive_calls, [])


class PreParseWrapsExpensiveHandlerTests(unittest.TestCase):
    """Matching marker → keepalive wraps *around* the handler invocation."""

    def test_research_turn_keepalive_wraps_handler(self) -> None:
        from yule_orchestrator.discord import member_bot as mb

        channel = _Channel()
        message = SimpleNamespace(
            content="[research-turn:s tech-lead] go", channel=channel
        )

        # Snapshot keepalive entry/exit order vs handler call.
        events: List[str] = []

        @contextlib.asynccontextmanager
        async def tracing_keepalive(ch, **kwargs):
            events.append("keepalive_enter")
            try:
                yield
            finally:
                events.append("keepalive_exit")

        def fake_research(**kwargs):
            events.append("handler_called")
            return SimpleNamespace(
                comment="x", session_id="s", message="post body"
            )

        async def fake_post(ch, outcome):
            events.append("post_called")

        with patch.object(
            mb, "handle_research_turn_message", side_effect=fake_research
        ), patch.object(
            mb, "_post_research_turn", side_effect=fake_post
        ), patch.object(mb, "typing_keepalive", tracing_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("tech-lead"), message=message
                )
            )

        # Critical ordering: keepalive must enter BEFORE the handler
        # runs (so typing is visible while expensive work happens).
        self.assertEqual(
            events,
            [
                "keepalive_enter",
                "handler_called",
                "post_called",
                "keepalive_exit",
            ],
        )

    def test_team_turn_keepalive_wraps_handler(self) -> None:
        from yule_orchestrator.discord import member_bot as mb

        channel = _Channel()
        message = SimpleNamespace(
            content="[team-turn:s tech-lead] go", channel=channel
        )

        events: List[str] = []

        @contextlib.asynccontextmanager
        async def tracing_keepalive(ch, **kwargs):
            events.append("keepalive_enter")
            try:
                yield
            finally:
                events.append("keepalive_exit")

        def fake_team(**kwargs):
            events.append("handler_called")
            return SimpleNamespace(
                turn=SimpleNamespace(session_id="s"),
                full_post=lambda: "body",
            )

        async def fake_post(ch, outcome):
            events.append("post_called")

        with patch.object(
            mb, "handle_research_turn_message", return_value=None
        ), patch.object(
            mb, "handle_team_turn_message", side_effect=fake_team
        ), patch.object(
            mb, "_post_team_turn", side_effect=fake_post
        ), patch.object(mb, "typing_keepalive", tracing_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("tech-lead"), message=message
                )
            )

        self.assertEqual(
            events,
            [
                "keepalive_enter",
                "handler_called",
                "post_called",
                "keepalive_exit",
            ],
        )

    def test_research_open_marker_role_agnostic_fires_keepalive(self) -> None:
        # [research-open:<sid>] has no role, so every active member
        # bot enters the keepalive path. Handler then enforces the
        # active_research_roles guard.
        from yule_orchestrator.discord import member_bot as mb

        channel = _Channel()
        message = SimpleNamespace(
            content="[research-open:sess-42] kickoff", channel=channel
        )

        events: List[str] = []

        @contextlib.asynccontextmanager
        async def tracing_keepalive(ch, **kwargs):
            events.append("keepalive_enter")
            try:
                yield
            finally:
                events.append("keepalive_exit")

        def fake_research(**kwargs):
            events.append("handler_called")
            return None  # handler decides this bot isn't active for this open

        with patch.object(
            mb, "handle_research_turn_message", side_effect=fake_research
        ), patch.object(
            mb, "handle_team_turn_message", return_value=None
        ), patch.object(mb, "typing_keepalive", tracing_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("ai-engineer"), message=message
                )
            )

        # Open-marker fires the keepalive even though the handler
        # returns None — this is the expected pre-gate behavior:
        # role-less open broadcasts always show typing while the
        # cheap handler check runs.
        self.assertIn("keepalive_enter", events)
        self.assertIn("handler_called", events)


if __name__ == "__main__":
    unittest.main()
