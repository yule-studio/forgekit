"""``yule runtime circuit reset`` CLI — A-M7-final tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.cli.main import main as cli_main
from yule_engineering.runtime.circuit_breaker import (
    CircuitBreakerPersistence,
)


class _CircuitCliFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "cache.sqlite3"
        self._env = mock.patch.dict(
            os.environ, {"YULE_CACHE_DB_PATH": str(self._db)}
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.persistence = CircuitBreakerPersistence(db_path=self._db)

    def _run_reset(self, *args: str):
        cli_args = [
            "--repo-root", str(self._tmp.name),
            "runtime", "circuit", "reset",
            *args,
        ]
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = cli_main(cli_args)
        return rc, buf_out.getvalue(), buf_err.getvalue()


class CircuitResetCommandTests(_CircuitCliFixture):
    def test_reset_clears_persisted_row(self) -> None:
        # Land an open row directly via the persistence helper, then
        # invoke the CLI.
        self.persistence.mark_open(
            service_id="eng-research-worker",
            opened_at=1000.0,
            last_reason="exit_code=1",
        )
        rc, out, _err = self._run_reset("eng-research-worker")
        self.assertEqual(rc, 0)
        self.assertIn("circuit-open state cleared", out)
        # Row gone.
        self.assertEqual(self.persistence.load_open_circuits(), ())

    def test_reset_when_nothing_open_succeeds_idempotently(self) -> None:
        rc, out, _err = self._run_reset("eng-research-worker")
        self.assertEqual(rc, 0)
        self.assertIn("no open circuit", out)

    def test_reset_unknown_service_returns_78(self) -> None:
        rc, _out, err = self._run_reset("eng-totally-bogus-service")
        self.assertEqual(rc, 78)
        self.assertIn("unknown service", err)

    def test_json_flag_emits_structured_payload(self) -> None:
        self.persistence.mark_open(
            service_id="eng-research-worker",
            opened_at=2000.0,
            last_reason="x",
        )
        rc, out, _err = self._run_reset(
            "eng-research-worker", "--json"
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["cleared"])
        self.assertEqual(payload["service_id"], "eng-research-worker")

    def test_json_flag_on_unknown_service_emits_error_payload(self) -> None:
        rc, out, _err = self._run_reset("eng-bogus", "--json")
        self.assertEqual(rc, 78)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "unknown_service")


if __name__ == "__main__":
    unittest.main()
