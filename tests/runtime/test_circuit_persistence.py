"""runtime.circuit_breaker persistence — A-M7-final unit tests.

Pin the SQLite-backed mirror so a sibling process (status CLI,
status poster) sees open circuits without poking the supervisor's
in-memory ledger:

  * ``record_restart`` that trips the breaker → row appears
  * sticky open: re-tripping doesn't duplicate; reset clears
  * ``CircuitBreakerRegistry`` constructed with a persistence
    pre-loads existing open rows (supervisor restart recovery)
  * ``load_persisted_circuit_snapshots`` projects to CircuitSnapshot
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_runtime.circuit_breaker import (
    CircuitBreakerPersistence,
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
    load_persisted_circuit_snapshots,
)


class _PersistenceFixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "cache.sqlite3"
        self.persistence = CircuitBreakerPersistence(db_path=self._db)


class PersistOnTripTests(_PersistenceFixture):
    def test_breaker_trip_writes_row(self) -> None:
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=1),
            persistence=self.persistence,
        )
        # Two restarts in window → trips breaker (max+1 events).
        registry.record_restart("eng-x", now=1000.0, reason="exit_code=1")
        registry.record_restart("eng-x", now=1001.0, reason="exit_code=1")
        rows = self.persistence.load_open_circuits()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].service_id, "eng-x")
        self.assertEqual(rows[0].opened_at, 1001.0)
        self.assertIn("exit_code=1", rows[0].last_reason)

    def test_in_memory_only_when_no_persistence_attached(self) -> None:
        # Sanity: registry without persistence still works (back-compat
        # with M7 callers that don't opt in).
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=1)
        )
        registry.record_restart("eng-x", now=1000.0)
        registry.record_restart("eng-x", now=1001.0)
        # No DB row.
        self.assertEqual(self.persistence.load_open_circuits(), ())

    def test_reset_clears_persisted_row(self) -> None:
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=1),
            persistence=self.persistence,
        )
        registry.record_restart("eng-y", now=1000.0)
        registry.record_restart("eng-y", now=1001.0)
        self.assertEqual(len(self.persistence.load_open_circuits()), 1)
        cleared = registry.reset("eng-y")
        self.assertTrue(cleared)
        self.assertEqual(self.persistence.load_open_circuits(), ())

    def test_reset_unknown_service_returns_false(self) -> None:
        registry = CircuitBreakerRegistry(persistence=self.persistence)
        self.assertFalse(registry.reset("eng-no-such-service"))


class LoadExistingTests(_PersistenceFixture):
    def test_supervisor_restart_recovers_open_circuits_from_db(self) -> None:
        # Simulate a previous supervisor run: persist an open row,
        # then construct a fresh registry pointing at the same DB.
        # The new registry should treat eng-z as already-open.
        self.persistence.mark_open(
            service_id="eng-z",
            opened_at=12345.0,
            last_reason="prior session",
        )
        registry = CircuitBreakerRegistry(persistence=self.persistence)
        self.assertTrue(registry.is_open("eng-z"))
        snap = registry.snapshot(now=99999.0)
        self.assertIn("eng-z", snap)
        self.assertTrue(snap["eng-z"].is_open)
        self.assertEqual(snap["eng-z"].opened_at, 12345.0)
        self.assertEqual(snap["eng-z"].last_reason, "prior session")


class LoadSnapshotsHelperTests(_PersistenceFixture):
    def test_helper_returns_circuit_snapshots(self) -> None:
        # Two open circuits — verify the helper projects both.
        self.persistence.mark_open(
            service_id="eng-a", opened_at=100.0, last_reason="a"
        )
        self.persistence.mark_open(
            service_id="eng-b", opened_at=200.0, last_reason="b"
        )
        snaps = load_persisted_circuit_snapshots(persistence=self.persistence)
        self.assertEqual(set(snaps.keys()), {"eng-a", "eng-b"})
        for snap in snaps.values():
            self.assertTrue(snap.is_open)
            self.assertEqual(snap.restart_count_in_window, 0)


if __name__ == "__main__":
    unittest.main()
