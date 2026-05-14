"""P0-E (#134 후속) — typing keepalive defense-in-depth 회귀.

두 가지 변경 사항을 보호:

  1. _handle_join_or_append 의 thread_continuation_fn 호출이
     typing_keepalive 로 wrap → long-running JOIN/APPEND 시 typing 유지.
  2. member bot 의 _dispatch_member_message 가 should_type_for_member_research
     helper 를 wiring → inactive role 에 대해 typing 차단 (post 는 유지).
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.discord.member.bot import (
    _dispatch_member_message,
    _resolve_active_roles_for_typing_gate,
)
from yule_orchestrator.discord.member.bots import MemberBotProfile


class _CountingChannel:
    """Tracks typing() enters + sent messages."""

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


# ---------------------------------------------------------------------------
# Commit 1 — _handle_join_or_append typing_keepalive wrap
# ---------------------------------------------------------------------------


class ThreadContinuationKeepaliveTests(unittest.TestCase):
    """`thread_continuation_fn` 의 long-running await 중 typing 유지 확인."""

    def test_join_or_append_wraps_thread_continuation_in_keepalive(self) -> None:
        from yule_orchestrator.discord import engineering_channel_router as router

        channel = _CountingChannel()
        message = SimpleNamespace(content="기존 세션 s", channel=channel)
        outcome = SimpleNamespace(
            write_requested=False,
            thread_topic=None,
            research_pack=None,
            collection_outcome=None,
        )
        decision = SimpleNamespace(action="JOIN_SESSION")

        async def slow_continuation(*, message, prompt, write_requested, thread_topic):
            # 0.12s 지연 → fast keepalive (interval=0.05) 에서 enters >= 2.
            await asyncio.sleep(0.12)
            return None  # 매칭 실패 → handler 가 None 반환, 다른 분기 발생 X

        async def fake_send_chunks(channel_arg, content, *args, **kwargs):
            channel_arg.sent.append(content)

        # patch typing_keepalive locally to use a 0.05s interval so we
        # can witness ≥2 enters during the 0.12s continuation work.
        from yule_orchestrator.discord.ui.typing_indicator import (
            typing_keepalive as original_keepalive,
        )

        @contextlib.asynccontextmanager
        async def fast_keepalive(ch, **kwargs):
            kwargs["interval"] = 0.05
            async with original_keepalive(ch, **kwargs):
                yield

        # The router module imports typing_keepalive *inside* the
        # function bodies, so we patch the typing_indicator module
        # symbol it pulls in.
        from yule_orchestrator.discord.ui import typing_indicator as ti_mod

        with patch.object(ti_mod, "typing_keepalive", fast_keepalive):
            _run(
                lambda: router._handle_join_or_append(
                    message=message,
                    outcome=outcome,
                    decision=decision,
                    intake_prompt="prompt",
                    send_chunks=fake_send_chunks,
                    thread_continuation_fn=slow_continuation,
                    research_loop_fn=None,
                )
            )

        # Keepalive 가 0.05s interval 로 0.12s 동안 떴다 → 최소 2회 enter.
        self.assertGreaterEqual(channel.enters, 2)


# ---------------------------------------------------------------------------
# Commit 2 — should_type_for_member_research wiring
# ---------------------------------------------------------------------------


class ActiveRolesGateTests(unittest.TestCase):
    """`_resolve_active_roles_for_typing_gate` best-effort 동작."""

    def test_missing_session_id_returns_none(self) -> None:
        self.assertIsNone(_resolve_active_roles_for_typing_gate(None))
        self.assertIsNone(_resolve_active_roles_for_typing_gate(""))

    def test_load_session_failure_returns_none(self) -> None:
        # load_session 예외 → 절대 raise 안 함, None 반환.
        from yule_orchestrator.discord.member import bot as mb

        def boom(_):
            raise RuntimeError("db down")

        with patch.object(mb, "load_session", side_effect=boom):
            result = _resolve_active_roles_for_typing_gate("sess-x")
        self.assertIsNone(result)

    def test_session_without_metadata_returns_none(self) -> None:
        from yule_orchestrator.discord.member import bot as mb

        session = SimpleNamespace(extra={})
        with patch.object(mb, "load_session", return_value=session):
            result = _resolve_active_roles_for_typing_gate("sess-x")
        self.assertIsNone(result)

    def test_session_with_persisted_roles_returns_tuple(self) -> None:
        from yule_orchestrator.discord.member import bot as mb

        session = SimpleNamespace(
            extra={"active_research_roles": ["tech-lead", "ai-engineer"]}
        )
        with patch.object(mb, "load_session", return_value=session):
            result = _resolve_active_roles_for_typing_gate("sess-x")
        self.assertEqual(result, ("tech-lead", "ai-engineer"))


class InactiveRoleSkipsTypingTests(unittest.TestCase):
    """active_research_roles 에 없는 role 은 typing 안 켜지지만 post 는 유지."""

    def test_inactive_role_posts_without_typing(self) -> None:
        from yule_orchestrator.discord.member import bot as mb

        channel = _CountingChannel()
        message = SimpleNamespace(
            content="[research-turn:sess-1 frontend-engineer] go", channel=channel
        )
        # frontend-engineer 가 active 아님 — tech-lead/ai-engineer 만.
        sentinel = SimpleNamespace(
            comment="x", session_id="sess-1", message="hi"
        )
        session = SimpleNamespace(
            extra={"active_research_roles": ["tech-lead", "ai-engineer"]}
        )

        keepalive_calls: list = []

        @contextlib.asynccontextmanager
        async def counting_keepalive(ch, **kwargs):
            keepalive_calls.append(kwargs.get("label"))
            yield

        async def fake_post(ch, outcome):
            await ch.send("posted")

        with patch.object(
            mb, "handle_research_turn_message", return_value=sentinel
        ), patch.object(mb, "load_session", return_value=session), patch.object(
            mb, "_post_research_turn", side_effect=fake_post
        ), patch.object(mb, "typing_keepalive", counting_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("frontend-engineer"), message=message
                )
            )

        # Post 는 발생 (handler 가 outcome 줬으니).
        self.assertEqual(channel.sent, ["posted"])
        # Gate False → keepalive wrap 미진입.
        self.assertEqual(keepalive_calls, [])

    def test_active_role_keeps_typing(self) -> None:
        # 같은 session 에서 active 인 role (ai-engineer) 으로 dispatch → keepalive wrap 진입.
        from yule_orchestrator.discord.member import bot as mb

        channel = _CountingChannel()
        message = SimpleNamespace(
            content="[research-turn:sess-1 ai-engineer] go", channel=channel
        )
        sentinel = SimpleNamespace(
            comment="x", session_id="sess-1", message="hi"
        )
        session = SimpleNamespace(
            extra={"active_research_roles": ["tech-lead", "ai-engineer"]}
        )

        keepalive_calls: list = []

        @contextlib.asynccontextmanager
        async def counting_keepalive(ch, **kwargs):
            keepalive_calls.append(kwargs.get("label"))
            yield

        async def fake_post(ch, outcome):
            await ch.send("posted")

        with patch.object(
            mb, "handle_research_turn_message", return_value=sentinel
        ), patch.object(mb, "load_session", return_value=session), patch.object(
            mb, "_post_research_turn", side_effect=fake_post
        ), patch.object(mb, "typing_keepalive", counting_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("ai-engineer"), message=message
                )
            )

        self.assertEqual(channel.sent, ["posted"])
        # Gate let the wrap fire — exactly one keepalive entry for research-turn.
        self.assertEqual(keepalive_calls, ["member:dispatch"])

    def test_legacy_session_no_metadata_keeps_typing(self) -> None:
        # active_research_roles 메타 없음 → helper 가 None → legacy fallback
        # (will_type=True) → keepalive wrap 진입.
        from yule_orchestrator.discord.member import bot as mb

        channel = _CountingChannel()
        message = SimpleNamespace(
            content="[research-turn:sess-legacy tech-lead] go", channel=channel
        )
        sentinel = SimpleNamespace(
            comment="x", session_id="sess-legacy", message="hi"
        )

        keepalive_calls: list = []

        @contextlib.asynccontextmanager
        async def counting_keepalive(ch, **kwargs):
            keepalive_calls.append(kwargs.get("label"))
            yield

        async def fake_post(ch, outcome):
            await ch.send("posted")

        with patch.object(
            mb, "handle_research_turn_message", return_value=sentinel
        ), patch.object(mb, "load_session", return_value=None), patch.object(
            mb, "_post_research_turn", side_effect=fake_post
        ), patch.object(mb, "typing_keepalive", counting_keepalive):
            _run(
                lambda: _dispatch_member_message(
                    profile=_profile("tech-lead"), message=message
                )
            )

        self.assertEqual(channel.sent, ["posted"])
        # Legacy → wrap fires.
        self.assertEqual(keepalive_calls, ["member:dispatch"])


if __name__ == "__main__":
    unittest.main()
