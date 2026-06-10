"""run_service entrypoint — A-M6.0 unit tests.

Pin the CLI behaviour: unknown / reserved service ids return exit
code 78 (so systemd's RestartPreventExitStatus=78 matches), and
parse_args_and_run forwards through to run_service_main with the
right kwargs.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.runtime.run_service import (
    EXIT_UNKNOWN_SERVICE,
    parse_args_and_run,
    run_service_main,
)


class UnknownServiceTests(unittest.TestCase):
    def test_unknown_service_returns_78(self) -> None:
        # systemd convention: 78 = EX_CONFIG, paired with
        # RestartPreventExitStatus=78 so the bad config doesn't
        # trigger infinite restart attempts.
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = run_service_main("eng-no-such-service")
        self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
        self.assertIn("unknown service", stderr.getvalue())

    def test_gateway_without_token_returns_78(self) -> None:
        # M6.1b-2 made eng-discord-gateway implemented. Calling it
        # without ENGINEERING_AGENT_BOT_GATEWAY_TOKEN returns 78
        # (EX_CONFIG) so systemd's RestartPreventExitStatus=78
        # keeps the failure visible to an operator instead of
        # burning restart attempts. Token is never echoed in the
        # error message.
        import os

        prev = os.environ.pop("ENGINEERING_AGENT_BOT_GATEWAY_TOKEN", None)
        try:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = run_service_main("eng-discord-gateway")
            self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
            self.assertIn(
                "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN", stderr.getvalue()
            )
        finally:
            if prev is not None:
                os.environ[
                    "ENGINEERING_AGENT_BOT_GATEWAY_TOKEN"
                ] = prev


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_dispatches_to_run_service_main(self) -> None:
        # Drive through parse_args_and_run with an unknown id so we
        # don't actually start an asyncio loop. We're proving the
        # argparse glue, not the worker body.
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = parse_args_and_run(["eng-totally-unknown"])
        self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)


if __name__ == "__main__":
    unittest.main()
