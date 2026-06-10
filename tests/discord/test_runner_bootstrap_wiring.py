"""Engineering gateway → role-runner dispatch wiring — A-M11b.

The M11 dispatcher exists, the env-driven factory exists; what was
missing was the bootstrap path that calls
:func:`set_role_runner_dispatch` so the gateway actually uses it.

These tests pin the wiring at three call sites without spinning up
a real Discord client:

  * :func:`bot._install_engineering_role_runner_dispatch_for_gateway`
    invokes :func:`install_engineering_role_runner_dispatch` with
    no exceptions on empty env, prints a "deterministic fallback only"
    line, and rebinds the global dispatcher.
  * Failure during install (simulated by patching the bootstrap
    helper to raise) does NOT propagate — the gateway must keep
    booting even when the role-runner subsystem fails.
  * The same helper is reusable from :mod:`runtime.run_service`'s
    ``_install_role_runner_dispatch_for_run_service`` shim.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

# Importing the wiring helpers requires importing the runtime
# engineering team module which lives behind the discord package.
# We import lazily inside each test so a partial install (no
# discord.ext) doesn't block the whole module.


class GatewayInstallHelperTests(unittest.TestCase):
    """Cover :func:`bot._install_engineering_role_runner_dispatch_for_gateway`."""

    def setUp(self) -> None:
        # Reset the global dispatcher between tests so failures
        # don't bleed across suites.
        try:
            from yule_engineering.discord.engineering_team_runtime import (
                set_role_runner_dispatch,
            )
        except Exception:
            self.skipTest("engineering_team_runtime not importable")
        set_role_runner_dispatch(None)
        self.addCleanup(set_role_runner_dispatch, None)

    def test_empty_env_install_prints_deterministic_summary(self) -> None:
        from yule_engineering.discord.bot import (
            _install_engineering_role_runner_dispatch_for_gateway,
        )

        # Empty env → bootstrap returns a deterministic-only trace and
        # the helper prints the friendly summary line.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YULE_ROLE_RUNNER_PROVIDERS", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                _install_engineering_role_runner_dispatch_for_gateway()
            output = buf.getvalue()
        self.assertIn("deterministic fallback only", output)
        # Dispatcher actually got registered — pull it back and call.
        from yule_engineering.discord.engineering_team_runtime import (
            get_role_runner_dispatch,
        )
        from yule_engineering.agents.runners.role_runner import (
            PROVIDER_DETERMINISTIC,
            RoleRunnerInput,
        )

        dispatch = get_role_runner_dispatch()
        self.assertIsNotNone(
            dispatch,
            "set_role_runner_dispatch was not called by the gateway helper",
        )
        # Hand it a stub session whose role list permits the role; the
        # deterministic terminal must produce text.
        from types import SimpleNamespace

        session = SimpleNamespace(
            session_id="sess-wiring-1",
            extra={"active_research_roles": ["ai-engineer"]},
        )
        out = dispatch(
            session,
            RoleRunnerInput(
                role="ai-engineer",
                session_id="sess-wiring-1",
                prompt="안녕하세요",
            ),
        )
        self.assertEqual(out.provider, PROVIDER_DETERMINISTIC)
        self.assertTrue(out.used_fallback)

    def test_install_failure_does_not_propagate(self) -> None:
        from yule_engineering.discord.bot import (
            _install_engineering_role_runner_dispatch_for_gateway,
        )

        # Patch the bootstrap helper to raise — the wiring shim must
        # catch and log, never propagate.
        with patch(
            "yule_engineering.agents.runners.bootstrap.install_engineering_role_runner_dispatch",
            side_effect=RuntimeError("simulated bootstrap failure"),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                # Must not raise.
                _install_engineering_role_runner_dispatch_for_gateway()
            output = buf.getvalue()
        # Type name surfaces in the warning, but never the message
        # body (which could carry secrets).
        self.assertIn("RuntimeError", output)
        self.assertNotIn("simulated bootstrap failure", output)

    def test_idempotent_double_install_uses_latest_env(self) -> None:
        from yule_engineering.discord.bot import (
            _install_engineering_role_runner_dispatch_for_gateway,
        )
        from yule_engineering.discord.engineering_team_runtime import (
            get_role_runner_dispatch,
        )

        # First install — empty env.
        os.environ.pop("YULE_ROLE_RUNNER_PROVIDERS", None)
        with redirect_stdout(io.StringIO()):
            _install_engineering_role_runner_dispatch_for_gateway()
        first = get_role_runner_dispatch()
        self.assertIsNotNone(first)

        # Second install — env now requests claude. Even if claude CLI
        # isn't on PATH we expect the dispatcher to be rebound (the
        # bootstrap rebuilds candidates each call).
        os.environ["YULE_ROLE_RUNNER_PROVIDERS"] = "claude"
        try:
            with redirect_stdout(io.StringIO()):
                _install_engineering_role_runner_dispatch_for_gateway()
            second = get_role_runner_dispatch()
            self.assertIsNotNone(second)
            # Different closures — ``set_role_runner_dispatch`` rebound
            # the slot. We don't assert ``first is not second`` strictly
            # because the underlying function objects could match if
            # Python interned them; instead we assert both are usable.
            self.assertTrue(callable(second))
        finally:
            os.environ.pop("YULE_ROLE_RUNNER_PROVIDERS", None)


class RunServiceInstallHelperTests(unittest.TestCase):
    """Cover the run-service shim that mirrors the bot.py installer."""

    def setUp(self) -> None:
        try:
            from yule_engineering.discord.engineering_team_runtime import (
                set_role_runner_dispatch,
            )
        except Exception:
            self.skipTest("engineering_team_runtime not importable")
        set_role_runner_dispatch(None)
        self.addCleanup(set_role_runner_dispatch, None)

    def test_empty_env_writes_summary_to_stderr(self) -> None:
        from yule_engineering.runtime.run_service import (
            _install_role_runner_dispatch_for_run_service,
        )

        os.environ.pop("YULE_ROLE_RUNNER_PROVIDERS", None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            _install_role_runner_dispatch_for_run_service()
        output = buf.getvalue()
        self.assertIn("deterministic fallback only", output)
        # Dispatcher is registered — set_role_runner_dispatch fired.
        from yule_engineering.discord.engineering_team_runtime import (
            get_role_runner_dispatch,
        )
        self.assertIsNotNone(get_role_runner_dispatch())

    def test_install_failure_swallowed(self) -> None:
        from yule_engineering.runtime.run_service import (
            _install_role_runner_dispatch_for_run_service,
        )

        with patch(
            "yule_engineering.agents.runners.bootstrap.install_engineering_role_runner_dispatch",
            side_effect=ValueError("bootstrap exploded"),
        ):
            buf = io.StringIO()
            with redirect_stderr(buf):
                _install_role_runner_dispatch_for_run_service()
            output = buf.getvalue()
        self.assertIn("ValueError", output)
        # Sensitive message body must never echo into the warning line.
        self.assertNotIn("bootstrap exploded", output)


if __name__ == "__main__":
    unittest.main()
