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

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.cli.main import main as cli_main


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


class CircuitOpenSurfaceTests(_CliFixture):
    """A-M7-final: persisted circuit-open status surfaces in CLI."""

    def test_status_text_render_marks_circuit_open_service(self) -> None:
        # Land an open circuit row directly in the persistence DB
        # (the same SQLite the CLI auto-loads via YULE_CACHE_DB_PATH).
        from yule_runtime.circuit_breaker import (
            CircuitBreakerPersistence,
        )

        persistence = CircuitBreakerPersistence(db_path=self._db)
        persistence.mark_open(
            service_id="eng-research-worker",
            opened_at=12345.0,
            last_reason="exit_code=1",
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
        # Service line shows CIRCUIT_OPEN, warnings call out the reset hint.
        self.assertIn("CIRCUIT_OPEN", text)
        self.assertIn("eng-research-worker", text)
        self.assertIn("yule runtime circuit reset", text)

    def test_status_json_carries_circuit_open_health(self) -> None:
        from yule_runtime.circuit_breaker import (
            CircuitBreakerPersistence,
        )

        persistence = CircuitBreakerPersistence(db_path=self._db)
        persistence.mark_open(
            service_id="eng-research-worker",
            opened_at=12345.0,
            last_reason="exit_code=1",
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
        healths = {
            row["service_id"]: row["health"] for row in payload["services"]
        }
        self.assertEqual(healths["eng-research-worker"], "circuit_open")


class RuntimePostDiscordTests(_CliFixture):
    """A-M7.1 ``--post-discord`` flag — drive the CLI end-to-end with
    an injected stub poster so the test never hits Discord.

    The CLI dispatches into ``run_runtime_status_command`` which
    accepts ``post_fn`` / ``state_store`` injection. We patch via
    ``mock.patch`` on the dispatch path so the argparse → function
    glue is also exercised.
    """

    def _build_status_args(self, *, force_post: bool = False):
        args = [
            "--repo-root", str(self._tmp.name),
            "runtime", "status",
            "--profile", "engineering",
            "--post-discord",
        ]
        if force_post:
            args.append("--force-post")
        return args

    def _patch_run_runtime_status_command(self, **overrides):
        """Wrap the real entrypoint so the CLI invokes it with our
        injected ``post_fn`` / ``state_store`` stubs.
        """
        from yule_engineering.cli import main as cli_module
        from yule_engineering.runtime import status_cli

        real = status_cli.run_runtime_status_command

        def wrapper(**kwargs):
            kwargs.update(overrides)
            return real(**kwargs)

        return mock.patch.object(
            cli_module,
            "run_runtime_status_command",  # not directly imported; fall through
            create=True,
            new=wrapper,
        )

    def test_post_discord_invokes_injected_post_fn(self) -> None:
        # Use a temp state file inside the test tmpdir so the CLI's
        # default state path doesn't pollute the operator cache.
        from yule_engineering.runtime.status_poster import (
            StatusPosterStateStore,
        )

        posted: List[str] = []

        async def fake_post(content: str):
            posted.append(content)
            return {"posted_message_id": 4242}

        state = StatusPosterStateStore(
            path=Path(self._tmp.name) / "poster_state.json"
        )

        # Patch the lazy-imported dispatch so our stub flows through.
        # We patch ``run_runtime_status_command`` at the cli.main
        # call site by monkey-injecting kwargs through a wrapper.
        from yule_engineering.runtime import status_cli as scl

        original = scl.run_runtime_status_command

        def wrapped(**kwargs):
            kwargs.setdefault("post_fn", fake_post)
            kwargs.setdefault("state_store", state)
            return original(**kwargs)

        with mock.patch.object(
            scl, "run_runtime_status_command", new=wrapped
        ):
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                rc = cli_main(self._build_status_args())

        self.assertEqual(rc, 0)
        # Status text still printed to stdout.
        self.assertIn("profile: engineering", buf_out.getvalue())
        # Post fired once and the state store now carries the dedup key.
        self.assertEqual(len(posted), 1)
        self.assertIn("runtime status", posted[0])
        self.assertIsNotNone(state.load().last_dedup_key)
        # Stderr names the post outcome (operator-friendly trace).
        self.assertIn("posted to #봇-상태", buf_err.getvalue())

    def test_post_discord_dedup_skips_second_identical_call(self) -> None:
        from yule_engineering.runtime.status_poster import (
            StatusPosterStateStore,
        )

        posted: List[str] = []

        async def fake_post(content: str):
            posted.append(content)
            return {"posted_message_id": 1}

        state = StatusPosterStateStore(
            path=Path(self._tmp.name) / "poster_state.json"
        )

        from yule_engineering.runtime import status_cli as scl

        original = scl.run_runtime_status_command

        def wrapped(**kwargs):
            kwargs.setdefault("post_fn", fake_post)
            kwargs.setdefault("state_store", state)
            return original(**kwargs)

        with mock.patch.object(
            scl, "run_runtime_status_command", new=wrapped
        ):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                cli_main(self._build_status_args())
                # Second call with the identical state must skip post.
                buf_err = io.StringIO()
                with redirect_stderr(buf_err):
                    cli_main(self._build_status_args())

        self.assertEqual(len(posted), 1)

    def test_post_discord_failure_prints_error_and_returns_nonzero(
        self,
    ) -> None:
        from yule_engineering.runtime.status_poster import (
            StatusPostError,
            StatusPosterStateStore,
        )

        async def broken_post(_content: str):
            raise StatusPostError("status_post_rate_limited")

        state = StatusPosterStateStore(
            path=Path(self._tmp.name) / "poster_state.json"
        )

        from yule_engineering.runtime import status_cli as scl

        original = scl.run_runtime_status_command

        def wrapped(**kwargs):
            kwargs.setdefault("post_fn", broken_post)
            kwargs.setdefault("state_store", state)
            return original(**kwargs)

        with mock.patch.object(
            scl, "run_runtime_status_command", new=wrapped
        ):
            buf_err = io.StringIO()
            buf_out = io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                rc = cli_main(self._build_status_args())

        self.assertEqual(rc, 1)
        # Status text still printed (operator wanted to see it).
        self.assertIn("profile: engineering", buf_out.getvalue())
        # Error message names the constant for journalctl grep.
        self.assertIn("status_post_rate_limited", buf_err.getvalue())
        # Failed post must NOT advance the dedup key.
        self.assertIsNone(state.load().last_dedup_key)


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
