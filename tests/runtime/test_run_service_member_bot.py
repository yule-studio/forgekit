"""F132 P0-C — ``run-service`` 의 DISCORD_MEMBER_BOT 분기 회귀.

The dispatcher hard rails (matching ``docs/operations.md``):

  * 토큰 envvar 비어있으면 EXIT_UNKNOWN_SERVICE (78) + stderr 안내.
  * 토큰이 placeholder shape (``<<TOKEN>>`` 등) 이면 78 + stderr 안내.
  * 유효 shape 의 토큰이면 ``run_member_bot_until_shutdown`` 호출.

테스트는 monkeypatch 로 ``run_member_bot_until_shutdown`` 을 mock 해서 실제
discord.py login 없이 분기 결정만 검증.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from typing import Any, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.runtime.run_service import (
    EXIT_OK,
    EXIT_UNKNOWN_SERVICE,
    _run_discord_member_bot,
)
from yule_runtime.services import ENGINEERING_PROFILE, ServiceKind


def _member_spec(role: str):
    for spec in ENGINEERING_PROFILE:
        if spec.kind == ServiceKind.DISCORD_MEMBER_BOT and spec.role == role:
            return spec
    raise AssertionError(f"no DISCORD_MEMBER_BOT row for role={role!r}")


def _run(coro_factory):
    """Run an async factory inside a fresh event loop.

    *coro_factory* is a zero-arg callable that returns a coroutine —
    deferring coroutine creation so any ``asyncio.Event()`` constructed
    inside binds to the loop that ``run_until_complete`` drives.
    Python 3.9's ``asyncio.Event`` reads ``events.get_event_loop()`` at
    construction time, so we set the new loop as the current loop
    before invoking the factory.
    """

    loop = asyncio.new_event_loop()
    previous = asyncio._get_running_loop()  # noqa: SLF001 - inspection only
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()
        if previous is None:
            asyncio.set_event_loop(None)


class _EnvSandbox:
    """Snapshot + restore the engineering member-bot env vars per test."""

    KEYS = (
        "ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN",
        "ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN",
        "ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN",
        "ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN",
        "ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN",
        "ENGINEERING_AGENT_BOT_FRONTEND_ENGINEER_TOKEN",
        "ENGINEERING_AGENT_BOT_PRODUCT_DESIGNER_TOKEN",
    )

    def __enter__(self) -> "_EnvSandbox":
        self._saved = {key: os.environ.get(key) for key in self.KEYS}
        for key in self.KEYS:
            os.environ.pop(key, None)
        return self

    def __exit__(self, *exc_info) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class GracefulDisableTests(unittest.TestCase):
    """Token missing / placeholder → exit 78 with operator-visible reason."""

    def setUp(self) -> None:
        # Snapshot stderr per-test so the redirect in each assertion
        # restores cleanly even when other tests in the full run
        # touched ``sys.stderr`` first.
        self._original_stderr = sys.stderr
        import io
        self._stderr_buf = io.StringIO()
        sys.stderr = self._stderr_buf

    def tearDown(self) -> None:
        sys.stderr = self._original_stderr

    def test_missing_token_exits_78(self) -> None:
        with _EnvSandbox():
            spec = _member_spec("tech-lead")
            rc = _run(lambda: _run_discord_member_bot(spec, shutdown_event=asyncio.Event()))
            self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
            self.assertIn("graceful-disable", self._stderr_buf.getvalue())
            self.assertIn(
                "ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN", self._stderr_buf.getvalue()
            )

    def test_placeholder_shape_exits_78(self) -> None:
        with _EnvSandbox():
            os.environ["ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN"] = (
                "<<NEW_BACKEND_TOKEN>>"
            )
            spec = _member_spec("backend-engineer")
            rc = _run(lambda: _run_discord_member_bot(spec, shutdown_event=asyncio.Event()))
            self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
            self.assertIn(
                "doesn't match the Discord bot token shape",
                self._stderr_buf.getvalue(),
            )

    def test_too_short_exits_78(self) -> None:
        # Short pasted snippet — fails the shape regex.
        with _EnvSandbox():
            os.environ["ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN"] = "abc"
            spec = _member_spec("qa-engineer")
            rc = _run(lambda: _run_discord_member_bot(spec, shutdown_event=asyncio.Event()))
            self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
            self.assertIn(
                "doesn't match the Discord bot token shape",
                self._stderr_buf.getvalue(),
            )

    def test_spec_without_role_exits_78(self) -> None:
        # Defensive: a malformed inventory row missing ``role`` (should
        # never happen) must not silently start an anonymous bot.
        from yule_runtime.services import ServiceSpec

        spec = ServiceSpec(
            service_id="eng-member-orphan",
            kind=ServiceKind.DISCORD_MEMBER_BOT,
            description="orphan",
            role=None,
        )
        rc = _run(lambda: _run_discord_member_bot(spec, shutdown_event=asyncio.Event()))
        self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
        self.assertIn("spec.role is empty", self._stderr_buf.getvalue())


class ValidTokenDispatchTests(unittest.TestCase):
    """Real-shape token → run_member_bot_until_shutdown invoked once."""

    def setUp(self) -> None:
        self._original_stderr = sys.stderr
        import io
        sys.stderr = io.StringIO()

    def tearDown(self) -> None:
        sys.stderr = self._original_stderr

    def test_valid_token_invokes_runner(self) -> None:
        from yule_discord.member import bot as member_bot_mod

        captured: dict = {}

        async def fake_runner(*, profile, shutdown_event, **kwargs):
            captured["profile"] = profile
            captured["shutdown_event"] = shutdown_event

        original = member_bot_mod.run_member_bot_until_shutdown
        member_bot_mod.run_member_bot_until_shutdown = fake_runner
        try:
            with _EnvSandbox():
                # 65-char base-style token matching the shape regex (3
                # segments separated by dots, head 20+ / mid 4+ / tail 20+).
                token = (
                    "AAAAAAAAAAAAAAAAAAAAAA"
                    ".BBBBB"
                    ".CCCCCCCCCCCCCCCCCCCCCCCCC"
                )
                os.environ[
                    "ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN"
                ] = token
                spec = _member_spec("devops-engineer")
                rc = _run(lambda: _run_discord_member_bot(spec, shutdown_event=asyncio.Event()))
                self.assertEqual(rc, EXIT_OK)
                self.assertIn("profile", captured)
                profile = captured["profile"]
                self.assertEqual(profile.role, "devops-engineer")
                self.assertEqual(profile.agent_id, "engineering-agent")
                self.assertEqual(profile.token, token)
        finally:
            member_bot_mod.run_member_bot_until_shutdown = original


if __name__ == "__main__":
    unittest.main()
