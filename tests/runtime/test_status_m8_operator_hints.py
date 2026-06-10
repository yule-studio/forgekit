"""M8 — runtime status operator-hint + smoke-checklist regressions.

The status surface is the operator's single screen for "is queue
processing happening, and if not, what command do I run?". M8
strengthens that surface with three specific behaviours:

  1. Each ServiceSpec's ``description`` is verbose enough that the
     status renderer can answer "what queue does this service
     process?" without reaching back to docs.
  2. STALE / UNKNOWN warnings include the exact restart command
     (``yule run-service`` / ``systemctl restart`` /
     ``yule runtime up``) — a bare "stale heartbeat: <id>" forced
     the operator to remember the next step from memory.
  3. The text render appends a 6-step live smoke checklist so the
     operator can copy commands from the same screen they used to
     read health.

Tests build minimal SQLite stores per case so heartbeat / queue math
stays deterministic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.store import JobQueue
from yule_runtime import services as svc_module
from yule_runtime.services import (
    ENGINEERING_PROFILE,
    ServiceKind,
    ServiceSpec,
)
from yule_engineering.runtime.status import (
    HEALTH_ALIVE,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    RuntimeStatusReport,
    ServiceStatus,
    build_runtime_status,
    render_live_smoke_checklist,
    render_runtime_status_text,
)


_FIXED_NOW: float = 1_731_000_000.0


def _spec(
    service_id: str,
    kind: ServiceKind,
    *,
    role: str = None,  # type: ignore[assignment]
    description: str = "test service",
) -> ServiceSpec:
    return ServiceSpec(
        service_id=service_id,
        kind=kind,
        description=description,
        role=role,
    )


class ServiceDescriptionContractTests(unittest.TestCase):
    """Each engineering service must advertise *what queue / role /
    Discord scope* it handles in its description so the status renderer
    can show `handles: …` without an extra lookup."""

    def test_research_worker_description_names_research_collect(self) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-research-worker"
        )
        self.assertIn("research_collect", spec.description)

    def test_role_worker_description_names_role_take_and_role(self) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-role-backend-engineer"
        )
        self.assertIn("role_take", spec.description)
        self.assertIn("backend-engineer", spec.description)

    def test_approval_worker_description_names_approval_post_and_channel(self) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-approval-worker"
        )
        self.assertIn("approval_post", spec.description)
        self.assertIn("승인-대기", spec.description)

    def test_obsidian_writer_description_names_obsidian_write_and_vault(
        self,
    ) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-obsidian-writer"
        )
        self.assertIn("obsidian_write", spec.description)
        self.assertIn("OBSIDIAN_VAULT_PATH", spec.description)

    def test_discord_gateway_description_names_intake_and_enqueue(self) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-discord-gateway"
        )
        self.assertIn("업무-접수", spec.description)
        # Gateway is NOT a queue consumer — must say so so the operator
        # doesn't expect it to drain a queue.
        self.assertIn("enqueue", spec.description.lower())

    def test_supervisor_watch_description_names_watchdog_role(self) -> None:
        spec = next(
            s for s in ENGINEERING_PROFILE
            if s.service_id == "eng-supervisor-watch"
        )
        self.assertIn("heartbeat", spec.description.lower())
        self.assertIn("lease", spec.description.lower())


class RenderTextSurfacesHandlesAndChecklistTests(unittest.TestCase):
    """Text render shows the per-service handles description AND the
    6-step smoke checklist, so the operator's recovery path stays on
    one screen."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._inventory = (
            _spec(
                "eng-research-worker",
                ServiceKind.RESEARCH_WORKER,
                description="research_collect queue consumer — sample",
            ),
        )
        self._queue = JobQueue(db_path=self._db)
        self._heartbeats = HeartbeatStore(db_path=self._db)

    def _build(self) -> RuntimeStatusReport:
        with mock.patch.dict(
            svc_module.PROFILES,
            {"m8-text": tuple(self._inventory)},
            clear=False,
        ):
            return build_runtime_status(
                profile="m8-text",
                queue=self._queue,
                heartbeats=self._heartbeats,
                now=_FIXED_NOW,
            )

    def test_render_includes_handles_line_per_service(self) -> None:
        text = render_runtime_status_text(self._build())
        # Each service block follows the "handles: <description>" line
        # so the operator never has to remember which queue the worker
        # owns.
        self.assertIn(
            "handles: research_collect queue consumer — sample",
            text,
        )

    def test_render_appends_live_smoke_checklist(self) -> None:
        text = render_runtime_status_text(self._build())
        self.assertIn("live smoke checklist:", text)
        # Checklist must reference all three operator entry points so
        # whichever environment the operator is on (dev parent /
        # systemd / one worker), the right command is on the screen.
        self.assertIn("yule runtime up", text)
        self.assertIn("yule run-service", text)
        self.assertIn("yule runtime status", text)
        # And the smoke flow itself: 업무-접수 enqueue → workers process.
        self.assertIn("업무-접수", text)


class RenderLiveSmokeChecklistTests(unittest.TestCase):
    """The checklist is a public surface — `yule runtime status`
    prints it AND the markdown summary may quote it. Pin the contract
    so docs / tests can rely on the 6-step shape."""

    def test_returns_six_numbered_steps(self) -> None:
        text = render_live_smoke_checklist()
        self.assertTrue(text.startswith("live smoke checklist:"))
        # 1. through 6. on individual lines.
        for number in range(1, 7):
            self.assertIn(f"{number}.", text)

    def test_step_one_starts_with_dry_run(self) -> None:
        text = render_live_smoke_checklist()
        # The very first step is `runtime up --dry-run` so the
        # operator confirms inventory before spawning anything.
        self.assertIn("--dry-run", text)


class StaleAndUnknownHintTests(unittest.TestCase):
    """STALE and UNKNOWN warnings must include the exact recovery
    command for whichever path the operator is running (single-host
    parent vs. systemd vs. one-worker foreground)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self._queue = JobQueue(db_path=self._db)
        self._heartbeats = HeartbeatStore(db_path=self._db)

    def _build(self, inventory) -> RuntimeStatusReport:
        with mock.patch.dict(
            svc_module.PROFILES,
            {"m8-warn": tuple(inventory)},
            clear=False,
        ):
            return build_runtime_status(
                profile="m8-warn",
                queue=self._queue,
                heartbeats=self._heartbeats,
                now=_FIXED_NOW,
            )

    def test_unknown_warning_lists_three_recovery_commands(self) -> None:
        # No heartbeat written ⇒ status will report UNKNOWN for the
        # single implemented service.
        report = self._build(
            (_spec("eng-research-worker", ServiceKind.RESEARCH_WORKER),)
        )
        self.assertEqual(len(report.warnings), 1)
        warning = report.warnings[0]
        self.assertIn("eng-research-worker", warning)
        # Operator hint surfaces for all three deployment paths so
        # whichever environment they're on, the command is right
        # there in the status output.
        self.assertIn("yule runtime up", warning)
        self.assertIn("yule run-service eng-research-worker", warning)
        self.assertIn(
            "yule-run-service@eng-research-worker.service", warning
        )

    def test_stale_warning_lists_recovery_commands(self) -> None:
        # Stamp a heartbeat older than the deadline so the status
        # comes back STALE. ``record`` writes ``last_beat=now``; we
        # patch ``time.time`` for the call so the heartbeat is dated
        # 10 minutes before the build's ``_FIXED_NOW``.
        with mock.patch("time.time", return_value=_FIXED_NOW - 600.0):
            self._heartbeats.record(
                service_id="eng-role-backend-engineer",
                pid=42,
            )
        report = self._build(
            (
                _spec(
                    "eng-role-backend-engineer",
                    ServiceKind.ROLE_WORKER,
                    role="backend-engineer",
                    description="role_take queue consumer — backend",
                ),
            )
        )
        self.assertEqual(len(report.warnings), 1)
        warning = report.warnings[0]
        self.assertIn("eng-role-backend-engineer", warning)
        self.assertIn("yule run-service eng-role-backend-engineer", warning)
        self.assertIn(
            "yule-run-service@eng-role-backend-engineer.service", warning
        )
        self.assertIn("yule runtime up", warning)


if __name__ == "__main__":
    unittest.main()
