"""Heartbeat store + supervisor sweep — A-M2 stabilisation.

Pin the contract that lets the supervisor decide which services are
alive and bounce expired-lease jobs in one watchdog tick.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.heartbeat import (
    DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    HeartbeatStore,
    run_supervisor_sweep,
)
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Both stores share one cache.sqlite3 — same convention as
        # production. Each test gets its own file via TemporaryDirectory.
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.heartbeats = HeartbeatStore(db_path=self._db)
        self.queue = JobQueue(db_path=self._db)


class HeartbeatRecordTests(_Fixture):
    def test_record_creates_row(self) -> None:
        record = self.heartbeats.record(
            "eng-gateway", pid=12345, metadata={"version": "0.1"}, now=1000.0
        )
        self.assertEqual(record.service_id, "eng-gateway")
        self.assertEqual(record.pid, 12345)
        self.assertEqual(record.last_beat, 1000.0)
        self.assertEqual(record.metadata.get("version"), "0.1")

    def test_record_upserts_latest(self) -> None:
        # Latest beat must overwrite — heartbeats are history-light.
        self.heartbeats.record("worker", now=1000.0)
        self.heartbeats.record("worker", now=1100.0)
        latest = self.heartbeats.get("worker")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.last_beat, 1100.0)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.heartbeats.get("missing"))

    def test_clear_drops_row(self) -> None:
        self.heartbeats.record("worker", now=1000.0)
        self.heartbeats.clear("worker")
        self.assertIsNone(self.heartbeats.get("worker"))


class IsAliveTests(_Fixture):
    def test_recent_beat_is_alive(self) -> None:
        record = self.heartbeats.record("worker", now=1000.0)
        self.assertTrue(record.is_alive(now=1010.0))

    def test_old_beat_is_dead_under_default_deadline(self) -> None:
        record = self.heartbeats.record("worker", now=1000.0)
        self.assertFalse(record.is_alive(now=1000.0 + DEFAULT_HEARTBEAT_DEADLINE_SECONDS + 5))


class StaleServicesTests(_Fixture):
    def test_lists_only_services_past_deadline(self) -> None:
        self.heartbeats.record("alive", now=1000.0)
        self.heartbeats.record("dead", now=900.0)  # 100s old
        stale = self.heartbeats.stale_services(deadline_seconds=60.0, now=1000.0)
        # Only the dead one shows up; the alive one is within deadline.
        self.assertEqual([r.service_id for r in stale], ["dead"])

    def test_never_registered_services_do_not_appear(self) -> None:
        # An intentionally-disabled role (no row in service_heartbeats)
        # must NOT count as stale — we only flag services that beat
        # at least once and then went quiet.
        self.heartbeats.record("alive", now=1000.0)
        stale = self.heartbeats.stale_services(deadline_seconds=60.0, now=1000.0)
        self.assertEqual(stale, ())


class SupervisorSweepTests(_Fixture):
    def test_sweep_reports_stale_and_reaps_leases(self) -> None:
        # eng-gateway last beat 200 s ago → stale at deadline 60 s.
        # eng-member just beat (sweep_now - 5 s) → alive.
        sweep_now = 2000.0
        self.heartbeats.record("eng-gateway", now=sweep_now - 200)
        self.heartbeats.record("eng-member", now=sweep_now - 5)
        # Make a job whose lease has expired by sweep_now.
        self.queue.enqueue(
            session_id="sess-1", job_type="role_take", now=sweep_now - 100
        )
        picked = self.queue.pick(worker_id="worker-A", now=sweep_now - 100)
        assert picked is not None
        report = run_supervisor_sweep(
            heartbeat_store=self.heartbeats,
            job_queue=self.queue,
            deadline_seconds=60.0,
            now=sweep_now,
        )
        # Supervisor sweep must surface BOTH signals: dead service +
        # job lease that timed out.
        self.assertEqual([r.service_id for r in report.stale], ["eng-gateway"])
        self.assertEqual(report.reaped_jobs, 1)
        # And the queue itself must have moved the job back to
        # failed_retryable so the next worker can retry.
        retried = self.queue.list_for_session(
            "sess-1", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(retried), 1)


if __name__ == "__main__":
    unittest.main()
