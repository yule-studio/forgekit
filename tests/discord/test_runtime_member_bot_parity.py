"""P0-C v2 (#132/#134) — runtime path member bot dispatch parity.

Before this PR, ``yule runtime up`` 의 ``build_member_bot`` factory 의
``on_message`` 가 placeholder 였다 (``_dispatch_member_message`` 가
``return None``). 그 결과 runtime 으로 spawn 된 member bot 이 Discord
멤버 리스트엔 보이는데 ``[research-*/team-*]`` directive 에 무반응.

본 test 는 hoist 된 ``_dispatch_member_message`` 가:

  1. ``[research-turn:*]`` directive 받으면 ``handle_research_turn_message``
     를 호출하고, outcome 이 있으면 ``_post_research_turn`` 으로 게시.
  2. ``[team-turn:*]`` directive 받으면 ``handle_team_turn_message``
     를 호출하고, outcome 이 있으면 ``_post_team_turn`` 으로 게시.
  3. inactive role (handler 가 None 반환) 시 silent — 게시 X.
  4. handler 가 예외 raise — silent + stderr 경고.
  5. post 가 예외 raise — channel 에 ``⚠️ 게시 실패`` fallback.

dev/test path (``yule discord up`` 의 sync ``run_member_bot``) 와 runtime
path (``build_member_bot``) 가 같은 dispatcher 함수를 호출하므로,
``_dispatch_member_message`` 만 검증해도 양쪽 parity 가 보장된다.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
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


class _FakeChannel:
    """Records ``send`` calls + provides a no-op typing context."""

    def __init__(self) -> None:
        self.sent: List[str] = []

    @contextlib.asynccontextmanager
    async def _typing_ctx(self):
        yield

    def typing(self):  # match discord.abc.Messageable shape
        return self._typing_ctx()

    async def send(self, content: str = "", **kwargs: Any) -> None:
        self.sent.append(content)


def _profile(role: str = "tech-lead") -> MemberBotProfile:
    return MemberBotProfile(
        agent_id="engineering-agent",
        role=role,
        env_key=f"ENGINEERING_AGENT_BOT_{role.upper().replace('-', '_')}_TOKEN",
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


class ResearchTurnDispatchTests(unittest.TestCase):
    def test_outcome_present_posts_research_turn(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(
            content="[research-turn:sess-1 tech-lead] go", channel=channel
        )
        sentinel = SimpleNamespace(comment="research take", session_id="sess-1")

        post_called: list = []

        async def fake_post(ch, outcome):
            post_called.append((ch, outcome))

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=sentinel,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_research_turn",
            side_effect=fake_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        self.assertEqual(len(post_called), 1)
        self.assertIs(post_called[0][1], sentinel)
        # No fallback ⚠️ message.
        self.assertFalse(any("⚠️" in s for s in channel.sent))

    def test_inactive_role_returns_none_stays_silent(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(
            content="[research-turn:sess-1 backend-engineer] go", channel=channel
        )

        post_called: list = []

        async def fake_post(ch, outcome):
            post_called.append((ch, outcome))

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=None,
        ), patch(
            "yule_orchestrator.discord.member_bot.handle_team_turn_message",
            return_value=None,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_research_turn",
            side_effect=fake_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        # Handler returned None → no post, no fallback.
        self.assertEqual(post_called, [])
        self.assertEqual(channel.sent, [])

    def test_handler_raises_logs_warning_and_falls_through(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(content="[research-turn:s t] go", channel=channel)

        original_stderr = sys.stderr
        import io
        sys.stderr = io.StringIO()
        try:
            with patch(
                "yule_orchestrator.discord.member_bot.handle_research_turn_message",
                side_effect=RuntimeError("boom"),
            ), patch(
                "yule_orchestrator.discord.member_bot.handle_team_turn_message",
                return_value=None,
            ):
                _run(lambda: _dispatch_member_message(
                    profile=_profile(), message=message
                ))
            warning_log = sys.stderr.getvalue()
        finally:
            sys.stderr = original_stderr

        self.assertIn("research handler failed", warning_log)
        self.assertIn("boom", warning_log)
        # Handler exception is caught — no fallback shown to user.
        self.assertEqual(channel.sent, [])

    def test_post_raises_surfaces_fallback_message(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(content="[research-turn:s t] go", channel=channel)
        sentinel = SimpleNamespace(comment="x", session_id="s")

        async def failing_post(ch, outcome):
            raise RuntimeError("network kapow")

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=sentinel,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_research_turn",
            side_effect=failing_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        self.assertEqual(len(channel.sent), 1)
        self.assertIn("댓글 게시 실패", channel.sent[0])
        self.assertIn("network kapow", channel.sent[0])


class TeamTurnDispatchTests(unittest.TestCase):
    def test_team_turn_outcome_posts(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(content="[team-turn:s tech-lead] go", channel=channel)
        sentinel = SimpleNamespace(turn_text="team take", session_id="s")

        post_called: list = []

        async def fake_post(ch, outcome):
            post_called.append((ch, outcome))

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=None,
        ), patch(
            "yule_orchestrator.discord.member_bot.handle_team_turn_message",
            return_value=sentinel,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_team_turn",
            side_effect=fake_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        self.assertEqual(len(post_called), 1)
        self.assertIs(post_called[0][1], sentinel)

    def test_team_turn_handler_raises_logs(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(content="[team-turn:s tech-lead] go", channel=channel)

        original_stderr = sys.stderr
        import io
        sys.stderr = io.StringIO()
        try:
            with patch(
                "yule_orchestrator.discord.member_bot.handle_research_turn_message",
                return_value=None,
            ), patch(
                "yule_orchestrator.discord.member_bot.handle_team_turn_message",
                side_effect=RuntimeError("kaboom"),
            ):
                _run(lambda: _dispatch_member_message(
                    profile=_profile(), message=message
                ))
            warning_log = sys.stderr.getvalue()
        finally:
            sys.stderr = original_stderr

        self.assertIn("team handler failed", warning_log)
        self.assertIn("kaboom", warning_log)

    def test_team_post_raises_surfaces_fallback(self) -> None:
        channel = _FakeChannel()
        message = SimpleNamespace(content="[team-turn:s tech-lead] go", channel=channel)
        sentinel = SimpleNamespace(turn_text="x", session_id="s")

        async def failing_post(ch, outcome):
            raise RuntimeError("net down")

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=None,
        ), patch(
            "yule_orchestrator.discord.member_bot.handle_team_turn_message",
            return_value=sentinel,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_team_turn",
            side_effect=failing_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        self.assertEqual(len(channel.sent), 1)
        self.assertIn("take 게시 실패", channel.sent[0])
        self.assertIn("net down", channel.sent[0])


class ResearchPrecedenceTests(unittest.TestCase):
    """Both markers in one message → research wins (first dispatch)."""

    def test_research_wins_when_both_present(self) -> None:
        channel = _FakeChannel()
        # research first, team second — handlers each match their own
        # marker; research returning non-None should short-circuit.
        message = SimpleNamespace(
            content="[research-turn:s tech-lead] x [team-turn:s tech-lead] y",
            channel=channel,
        )
        research_sentinel = SimpleNamespace(comment="r", session_id="s")
        team_called: list = []

        async def fake_research_post(ch, outcome):
            pass

        async def fake_team_post(ch, outcome):
            team_called.append(outcome)

        with patch(
            "yule_orchestrator.discord.member_bot.handle_research_turn_message",
            return_value=research_sentinel,
        ), patch(
            "yule_orchestrator.discord.member_bot.handle_team_turn_message",
            return_value=SimpleNamespace(turn_text="t", session_id="s"),
        ), patch(
            "yule_orchestrator.discord.member_bot._post_research_turn",
            side_effect=fake_research_post,
        ), patch(
            "yule_orchestrator.discord.member_bot._post_team_turn",
            side_effect=fake_team_post,
        ):
            _run(lambda: _dispatch_member_message(
                profile=_profile(), message=message
            ))

        # team_outcome was never asked because research took precedence.
        self.assertEqual(team_called, [])


if __name__ == "__main__":
    unittest.main()
