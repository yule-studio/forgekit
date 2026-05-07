"""runtime status CLI — A-M6.3 integration tests.

End-to-end smoke for ``yule runtime status`` so the argparse +
adapter wiring stays load-bearing:

  * argparse routes the command without exploding on optional flags
  * ``--json`` produces parseable output
  * unknown profile yields exit 78 (mirrors ``run-service``)
  * legacy ``runtime up`` parsing still works (no regression)

The CLI dispatcher imports a lot of optional modules at startup.
We only invoke ``runtime status`` so heavy import paths don't fire.
"""

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

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.cli.main import main as cli_main


class _CliFixture(unittest.TestCase):
    """Per-test temp SQLite + YULE_CACHE_DB_PATH redirect.

    Without redirecting, the CLI would default to whichever path
    the test bootstrap set, and we'd see whatever stale rows live
    in that file. Pinning to a fresh DB makes the assertions
    deterministic.
    """

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._env_patch = mock.patch.dict(
            os.environ, {"YULE_CACHE_DB_PATH": str(self._db)}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Pre-create stores so the schema exists before the CLI reads.
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)


class RuntimeStatusCliTests(_CliFixture):
    def test_json_flag_emits_valid_payload(self) -> None:
        # Seed enough state that every section has content.
        self.heartbeats.record(
            "eng-research-worker", pid=4242
        )
        self.queue.enqueue(
            session_id="s1", job_type="research_collect"
        )

        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            rc = cli_main(
                [
                    "--repo-root", str(self._tmp.name),
                    "runtime", "status",
                    "--profile", "engineering",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(buf_out.getvalue())
        self.assertEqual(payload["profile"], "engineering")
        # Every engineering service shows up.
        ids = {s["service_id"] for s in payload["services"]}
        self.assertIn("eng-research-worker", ids)
        self.assertIn("eng-discord-gateway", ids)
        # The seeded queued row appears.
        job_types = {j["job_type"] for j in payload["job_types"]}
        self.assertIn("research_collect", job_types)

    def test_text_render_default_is_human_readable(self) -> None:
        self.heartbeats.record(
            "eng-approval-worker", pid=99
        )
        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            rc = cli_main(
                [
                    "--repo-root", str(self._tmp.name),
                    "runtime", "status",
                    "--profile", "engineering",
                ]
            )
        self.assertEqual(rc, 0)
        text = buf_out.getvalue()
        self.assertIn("profile: engineering", text)
        self.assertIn("services:", text)
        self.assertIn("queue:", text)
        # Recorded heartbeat surfaces as ALIVE.
        self.assertIn("eng-approval-worker", text)
        self.assertIn("ALIVE", text)

    def test_unknown_profile_returns_exit_78(self) -> None:
        buf_err = io.StringIO()
        buf_out = io.StringIO()
        with redirect_stderr(buf_err), redirect_stdout(buf_out):
            rc = cli_main(
                [
                    "--repo-root", str(self._tmp.name),
                    "runtime", "status",
                    "--profile", "no-such-profile",
                ]
            )
        # Same exit-code convention as run-service so systemd's
        # RestartPreventExitStatus=78 catches the typo.
        self.assertEqual(rc, 78)
        self.assertIn("unknown profile", buf_err.getvalue())

    def test_status_command_does_not_mutate_any_job_state(self) -> None:
        # Seed a queued row, run status, verify the row's state
        # didn't move (read-only invariant).
        job = self.queue.enqueue(
            session_id="s-readonly", job_type="research_collect"
        )
        before = self.queue.get(job.job_id)
        assert before is not None

        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            cli_main(
                [
                    "--repo-root", str(self._tmp.name),
                    "runtime", "status",
                    "--profile", "engineering",
                    "--json",
                ]
            )
        after = self.queue.get(job.job_id)
        assert after is not None
        self.assertEqual(after.state, before.state)
        self.assertEqual(after.attempt, before.attempt)
        self.assertEqual(after.updated_at, before.updated_at)


class RuntimeUpDryRunStillWorksTests(_CliFixture):
    """Regression guard — adding ``status`` must not break ``up --list``."""

    def test_runtime_up_dry_run_still_renders(self) -> None:
        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            rc = cli_main(
                [
                    "--repo-root", str(self._tmp.name),
                    "runtime", "up",
                    "--profile", "engineering",
                    "--dry-run",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("profile: engineering", buf_out.getvalue())
        self.assertIn("services to start", buf_out.getvalue())


if __name__ == "__main__":
    unittest.main()
