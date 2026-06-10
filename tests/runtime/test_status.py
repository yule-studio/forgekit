"""runtime status builder + renderers — A-M6.3 unit tests.

Pin the contract :func:`build_runtime_status` exposes:

  * Service health: ``alive`` / ``stale`` / ``unknown`` /
    ``reserved`` based on heartbeat age vs. deadline +
    inventory implementation flag.
  * Queue summary: per-job-type counts (queued / in_progress /
    saved / failed_retryable / failed_terminal) + oldest queued
    age.
  * Recent failures: most-recent-first list of FAILED rows with
    error string preserved.
  * Warnings: stale heartbeat / unknown implemented service /
    failed_terminal rows.

Tests pass in fixed ``now`` so age math is deterministic. Fresh
SQLite per test class for isolation.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.runtime import services as svc_module
from yule_engineering.runtime.services import (
    ServiceKind,
    ServiceSpec,
)
from yule_engineering.runtime.status import (
    HEALTH_ALIVE,
    HEALTH_RESERVED,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    RuntimeStatusReport,
    build_runtime_status,
    render_runtime_status_json,
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


class _StatusFixture(unittest.TestCase):
    """Tiny inventory + per-test SQLite for the status builder."""

    PROFILE_NAME: str = "test-profile-status"

    DEFAULT_SPECS = (
        _spec("eng-supervisor-watch", ServiceKind.SUPERVISOR),
        _spec("eng-research-worker", ServiceKind.RESEARCH_WORKER),
        _spec(
            "eng-role-tech-lead",
            ServiceKind.ROLE_WORKER,
            role="tech-lead",
        ),
        _spec("eng-approval-worker", ServiceKind.APPROVAL_WORKER),
        _spec("eng-obsidian-writer", ServiceKind.OBSIDIAN_WRITER),
    )

    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)

        # Register an isolated profile so the status builder doesn't
        # reach the real ENGINEERING_PROFILE rows.
        self._profile_patch = mock.patch.dict(
            svc_module.PROFILES,
            {self.PROFILE_NAME: self.DEFAULT_SPECS},
        )
        self._profile_patch.start()
        self.addCleanup(self._profile_patch.stop)


# ---------------------------------------------------------------------------
# Health labels
# ---------------------------------------------------------------------------


class ServiceHealthTests(_StatusFixture):
    def test_alive_when_heartbeat_within_deadline(self) -> None:
        self.heartbeats.record(
            "eng-research-worker", pid=4242, now=_FIXED_NOW - 10.0
        )
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            deadline_seconds=90.0,
            now=_FIXED_NOW,
        )
        research = next(
            s for s in report.services if s.service_id == "eng-research-worker"
        )
        self.assertEqual(research.health, HEALTH_ALIVE)
        self.assertEqual(research.pid, 4242)
        self.assertAlmostEqual(research.heartbeat_age_seconds, 10.0)

    def test_stale_when_heartbeat_past_deadline(self) -> None:
        self.heartbeats.record(
            "eng-approval-worker", pid=10, now=_FIXED_NOW - 200.0
        )
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            deadline_seconds=90.0,
            now=_FIXED_NOW,
        )
        approval = next(
            s for s in report.services if s.service_id == "eng-approval-worker"
        )
        self.assertEqual(approval.health, HEALTH_STALE)
        # Renderer warning surfaces this.
        self.assertTrue(
            any("stale heartbeat" in w for w in report.warnings),
            report.warnings,
        )

    def test_unknown_when_no_heartbeat_recorded(self) -> None:
        # No heartbeats at all — every implemented service is unknown.
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            deadline_seconds=90.0,
            now=_FIXED_NOW,
        )
        for svc in report.services:
            with self.subTest(service_id=svc.service_id):
                self.assertEqual(svc.health, HEALTH_UNKNOWN)
                self.assertIsNone(svc.heartbeat_age_seconds)
                self.assertIsNone(svc.pid)
        self.assertTrue(
            any("no heartbeat" in w for w in report.warnings),
            report.warnings,
        )

    def test_reserved_service_carries_reserved_health(self) -> None:
        # Patch in a profile with a RESERVED placeholder to verify
        # the renderer treats it separately from implemented rows.
        reserved_spec = _spec(
            "eng-future-thing",
            ServiceKind.RESERVED_DISCORD_GATEWAY,
            description="reserved placeholder",
        )
        with mock.patch.dict(
            svc_module.PROFILES,
            {self.PROFILE_NAME: (reserved_spec,)},
        ):
            report = build_runtime_status(
                profile=self.PROFILE_NAME,
                queue=self.queue,
                heartbeats=self.heartbeats,
                now=_FIXED_NOW,
            )
        self.assertEqual(len(report.services), 1)
        only = report.services[0]
        self.assertFalse(only.implemented)
        self.assertEqual(only.health, HEALTH_RESERVED)
        # Reserved services don't trigger the "no heartbeat" warning —
        # they're inventory placeholders, not workers we expected to run.
        self.assertFalse(
            any("no heartbeat" in w for w in report.warnings),
            report.warnings,
        )

    def test_role_filter_propagates_to_status_row(self) -> None:
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        role_row = next(
            s for s in report.services if s.service_id == "eng-role-tech-lead"
        )
        self.assertEqual(role_row.role, "tech-lead")
        # role workers carry the role_take job_type pointer.
        self.assertEqual(role_row.job_type, "role_take")


# ---------------------------------------------------------------------------
# Queue summary
# ---------------------------------------------------------------------------


class QueueSummaryTests(_StatusFixture):
    def test_per_job_type_counts_aggregate_across_states(self) -> None:
        # Seed three rows: 1 queued, 1 in_progress, 1 saved for the
        # same job_type. Only saved counts under saved; queued under
        # queued; in_progress under in_progress.
        # ResearchWorker pattern: enqueue, pick → ASSIGNED, transition
        # to IN_PROGRESS, then SAVED.
        a = self.queue.enqueue(
            session_id="s1",
            job_type="research_collect",
            now=_FIXED_NOW - 30.0,
        )
        b = self.queue.enqueue(
            session_id="s2",
            job_type="research_collect",
            now=_FIXED_NOW - 60.0,
        )
        # Drive `b` to in_progress.
        self.queue.transition(b.job_id, JobState.ASSIGNED, now=_FIXED_NOW)  # type: ignore[arg-type]
        # Wait — transition direct from QUEUED to ASSIGNED requires
        # pick. Use pick to claim.
        # Reset: re-enqueue scheme — easier via pick + transition.
        # Actually `transition(QUEUED → ASSIGNED)` IS allowed per the
        # state machine, even without a pick. Confirm by trying
        # transition direct.
        self.queue.transition(b.job_id, JobState.IN_PROGRESS, now=_FIXED_NOW)
        # Drive a second SAVED row through the supported path.
        c = self.queue.enqueue(
            session_id="s3",
            job_type="research_collect",
            now=_FIXED_NOW - 90.0,
        )
        self.queue.transition(c.job_id, JobState.ASSIGNED, now=_FIXED_NOW)
        self.queue.transition(c.job_id, JobState.IN_PROGRESS, now=_FIXED_NOW)
        self.queue.transition(c.job_id, JobState.SAVED, now=_FIXED_NOW)

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        research_summary = next(
            j for j in report.job_types if j.job_type == "research_collect"
        )
        self.assertEqual(research_summary.queued, 1)
        self.assertEqual(research_summary.in_progress, 1)
        self.assertEqual(research_summary.saved, 1)
        # Oldest queued row is `a` (only one still QUEUED), seeded 30s
        # ago — matches the queue's available_at timestamp.
        self.assertAlmostEqual(
            research_summary.oldest_queued_age_seconds, 30.0, places=2
        )

    def test_failed_retryable_and_terminal_are_separated(self) -> None:
        retry = self.queue.enqueue(
            session_id="s1",
            job_type="role_take",
            role="tech-lead",
            now=_FIXED_NOW,
        )
        self.queue.transition(retry.job_id, JobState.ASSIGNED, now=_FIXED_NOW)
        self.queue.transition(
            retry.job_id, JobState.FAILED_RETRYABLE,
            result={"error": "ProviderError: 500"},
            now=_FIXED_NOW,
        )

        terminal = self.queue.enqueue(
            session_id="s2",
            job_type="role_take",
            role="tech-lead",
            now=_FIXED_NOW,
        )
        self.queue.transition(
            terminal.job_id, JobState.FAILED_TERMINAL, now=_FIXED_NOW
        )

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        role_summary = next(
            j for j in report.job_types if j.job_type == "role_take"
        )
        self.assertEqual(role_summary.failed_retryable, 1)
        self.assertEqual(role_summary.failed_terminal, 1)
        # Warning surfaces failed_terminal so the operator notices.
        self.assertTrue(
            any("failed_terminal" in w for w in report.warnings),
            report.warnings,
        )

    def test_empty_queue_yields_zero_row_canonical_job_types(self) -> None:
        # Previously this asserted ``report.job_types == ()`` — meaning the
        # operator surface hid every job type until it had work. That
        # made "executor wired but idle" indistinguishable from "executor
        # missing", which is the exact ambiguity the coding_execute
        # operator-surface fix targets. Canonical job types are now always
        # surfaced with zero rows.
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            now=_FIXED_NOW,
        )
        types_seen = {jt.job_type for jt in report.job_types}
        self.assertTrue(
            {
                "research_collect",
                "role_take",
                "approval_post",
                "obsidian_write",
                "coding_execute",
            }.issubset(types_seen),
            msg=f"missing canonical job types: {types_seen}",
        )
        for jt in report.job_types:
            self.assertEqual(jt.queued, 0)
            self.assertEqual(jt.in_progress, 0)
            self.assertEqual(jt.failed_terminal, 0)


# ---------------------------------------------------------------------------
# Failed-recent listing
# ---------------------------------------------------------------------------


class FailedRecentTests(_StatusFixture):
    def test_recent_failed_rows_carry_error_and_age(self) -> None:
        # Land two FAILED_RETRYABLE rows at different timestamps.
        first = self.queue.enqueue(
            session_id="s1",
            job_type="research_collect",
            now=_FIXED_NOW - 120.0,
        )
        self.queue.transition(first.job_id, JobState.ASSIGNED, now=_FIXED_NOW - 110.0)
        self.queue.transition(
            first.job_id,
            JobState.FAILED_RETRYABLE,
            result={"error": "TimeoutError: provider"},
            now=_FIXED_NOW - 100.0,
        )

        second = self.queue.enqueue(
            session_id="s2",
            job_type="role_take",
            role="qa-engineer",
            now=_FIXED_NOW - 30.0,
        )
        self.queue.transition(second.job_id, JobState.ASSIGNED, now=_FIXED_NOW - 25.0)
        self.queue.transition(
            second.job_id,
            JobState.FAILED_RETRYABLE,
            result={"error": "RuntimeError: policy mismatch"},
            now=_FIXED_NOW - 5.0,
        )

        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=10,
            now=_FIXED_NOW,
        )
        # Most recent first.
        self.assertEqual(len(report.failed_recent), 2)
        self.assertEqual(report.failed_recent[0].job_id, second.job_id)
        self.assertEqual(report.failed_recent[0].role, "qa-engineer")
        self.assertEqual(
            report.failed_recent[0].error, "RuntimeError: policy mismatch"
        )
        self.assertAlmostEqual(report.failed_recent[0].age_seconds, 5.0)
        self.assertEqual(report.failed_recent[1].job_id, first.job_id)
        self.assertEqual(
            report.failed_recent[1].error, "TimeoutError: provider"
        )

    def test_failed_limit_caps_the_list(self) -> None:
        for idx in range(5):
            j = self.queue.enqueue(
                session_id=f"s{idx}",
                job_type="approval_post",
                now=_FIXED_NOW - 100.0 + idx,
            )
            self.queue.transition(
                j.job_id, JobState.ASSIGNED, now=_FIXED_NOW - 50.0 + idx
            )
            self.queue.transition(
                j.job_id,
                JobState.FAILED_RETRYABLE,
                result={"error": f"err-{idx}"},
                now=_FIXED_NOW - 10.0 + idx * 0.1,
            )
        report = build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=2,
            now=_FIXED_NOW,
        )
        self.assertEqual(len(report.failed_recent), 2)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class RendererTests(_StatusFixture):
    def _seed_minimal_report(self) -> RuntimeStatusReport:
        # alive + stale + unknown all represented so the renderer
        # exercises every branch.
        self.heartbeats.record(
            "eng-research-worker", pid=11, now=_FIXED_NOW - 5.0
        )
        self.heartbeats.record(
            "eng-approval-worker", pid=12, now=_FIXED_NOW - 500.0
        )
        # Land a queued + a failed_terminal so summary + warning fire.
        self.queue.enqueue(
            session_id="s1",
            job_type="research_collect",
            now=_FIXED_NOW - 20.0,
        )
        terminal = self.queue.enqueue(
            session_id="s2",
            job_type="role_take",
            role="ai-engineer",
            now=_FIXED_NOW - 30.0,
        )
        self.queue.transition(
            terminal.job_id,
            JobState.FAILED_TERMINAL,
            result={"error": "BackendError: capacity exceeded"},
            now=_FIXED_NOW - 5.0,
        )
        return build_runtime_status(
            profile=self.PROFILE_NAME,
            queue=self.queue,
            heartbeats=self.heartbeats,
            deadline_seconds=90.0,
            now=_FIXED_NOW,
        )

    def test_text_render_contains_each_section(self) -> None:
        report = self._seed_minimal_report()
        text = render_runtime_status_text(report)
        self.assertIn(f"profile: {self.PROFILE_NAME}", text)
        self.assertIn("services:", text)
        self.assertIn("queue:", text)
        self.assertIn("recent failures:", text)
        self.assertIn("warnings:", text)
        # Specific lines surface the alive + stale + unknown trio.
        self.assertIn("ALIVE", text)
        self.assertIn("STALE", text)
        self.assertIn("UNKNOWN", text)
        # Failed_terminal warning surfaces.
        self.assertIn("failed_terminal", text)
        self.assertIn("BackendError: capacity exceeded", text)

    def test_json_render_round_trips_to_dict(self) -> None:
        report = self._seed_minimal_report()
        payload: Dict[str, Any] = json.loads(
            render_runtime_status_json(report)
        )
        # Top-level keys.
        self.assertEqual(payload["profile"], self.PROFILE_NAME)
        self.assertEqual(payload["deadline_seconds"], 90.0)
        # Service health labels survive serialisation.
        healths = {
            row["service_id"]: row["health"] for row in payload["services"]
        }
        self.assertEqual(healths["eng-research-worker"], HEALTH_ALIVE)
        self.assertEqual(healths["eng-approval-worker"], HEALTH_STALE)
        self.assertEqual(healths["eng-supervisor-watch"], HEALTH_UNKNOWN)
        # Queue summary is a list with stable structure.
        job_types = {row["job_type"]: row for row in payload["job_types"]}
        self.assertEqual(job_types["research_collect"]["queued"], 1)
        self.assertEqual(job_types["role_take"]["failed_terminal"], 1)
        # Failed-recent list preserves error string.
        self.assertEqual(len(payload["failed_recent"]), 1)
        self.assertEqual(
            payload["failed_recent"][0]["error"],
            "BackendError: capacity exceeded",
        )
        self.assertGreater(len(payload["warnings"]), 0)


# ---------------------------------------------------------------------------
# Profile validation
# ---------------------------------------------------------------------------


class UnknownProfileTests(_StatusFixture):
    def test_unknown_profile_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_runtime_status(
                profile="not-a-real-profile",
                queue=self.queue,
                heartbeats=self.heartbeats,
                now=_FIXED_NOW,
            )


if __name__ == "__main__":
    unittest.main()
