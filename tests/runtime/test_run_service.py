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

from yule_orchestrator.runtime.run_service import (
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

    def test_reserved_service_returns_78(self) -> None:
        # Gateway is in the inventory but not implemented yet (M6.1).
        # run_service must refuse it loudly with the same exit code
        # as unknown ids — the operator's recovery is "wait for M6.1
        # or use yule discord up", not "retry".
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = run_service_main("eng-discord-gateway")
        self.assertEqual(rc, EXIT_UNKNOWN_SERVICE)
        self.assertIn("reserved", stderr.getvalue())


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
