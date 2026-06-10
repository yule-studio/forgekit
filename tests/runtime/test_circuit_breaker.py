"""runtime.circuit_breaker — A-M7 unit tests.

Pin the policy contract:

  * 5 restarts within 5-minute window → circuit opens
  * Sliding window — restarts older than the window don't count
  * ``is_open`` is sticky until ``reset``
  * ``snapshot`` exposes a read-only view consumed by status surfaces

Clock is injected so the threshold logic is deterministic.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.runtime.circuit_breaker import (
    DEFAULT_CIRCUIT_MAX_RESTARTS,
    DEFAULT_CIRCUIT_WINDOW_SECONDS,
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
)


class CircuitBreakerThresholdTests(unittest.TestCase):
    def test_opens_after_max_plus_one_within_window(self) -> None:
        # Default policy: 5 restarts in 300s. 6th restart trips it.
        registry = CircuitBreakerRegistry()
        now = 1_000.0
        for i in range(DEFAULT_CIRCUIT_MAX_RESTARTS):
            registry.record_restart("eng-x", now=now + i * 10.0)
            self.assertFalse(registry.is_open("eng-x"))
        # The (max_restarts + 1)-th restart trips it.
        registry.record_restart(
            "eng-x", now=now + DEFAULT_CIRCUIT_MAX_RESTARTS * 10.0
        )
        self.assertTrue(registry.is_open("eng-x"))

    def test_window_eviction_keeps_old_restarts_from_counting(self) -> None:
        # 5 restarts spread over an hour shouldn't trip a 5-minute window.
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=300.0, max_restarts=5)
        )
        base = 0.0
        for i in range(6):
            # Each restart 120s apart — within 5 minutes only 3 can
            # coexist at any time, so the breaker never trips.
            registry.record_restart("eng-y", now=base + i * 120.0)
        self.assertFalse(registry.is_open("eng-y"))

    def test_is_open_is_sticky(self) -> None:
        # Once opened, recording a 7th restart doesn't accidentally
        # reset the counter (events stay > threshold inside window).
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=300.0, max_restarts=5)
        )
        for i in range(6):
            registry.record_restart("eng-z", now=1_000.0 + i)
        self.assertTrue(registry.is_open("eng-z"))
        # Record one more in the future; still open.
        registry.record_restart("eng-z", now=1_300.0)
        self.assertTrue(registry.is_open("eng-z"))

    def test_reset_clears_state(self) -> None:
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=2)
        )
        for i in range(5):
            registry.record_restart("eng-a", now=10.0 + i)
        self.assertTrue(registry.is_open("eng-a"))
        registry.reset("eng-a")
        self.assertFalse(registry.is_open("eng-a"))


class CircuitBreakerSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_open_with_window_count(self) -> None:
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=2)
        )
        # Three restarts in 5 seconds — trips a 2-restart threshold.
        for offset in range(3):
            registry.record_restart(
                "eng-b",
                now=100.0 + offset,
                reason=f"exit_code={offset}",
            )
        snap = registry.snapshot(now=120.0)
        self.assertIn("eng-b", snap)
        self.assertTrue(snap["eng-b"].is_open)
        self.assertEqual(snap["eng-b"].restart_count_in_window, 3)
        # Last reason captured at the moment the breaker tripped.
        self.assertEqual(snap["eng-b"].last_reason, "exit_code=2")
        self.assertIsNotNone(snap["eng-b"].opened_at)

    def test_snapshot_skips_unknown_services(self) -> None:
        registry = CircuitBreakerRegistry()
        self.assertEqual(registry.snapshot(), {})

    def test_snapshot_window_count_drops_after_window(self) -> None:
        # After the window passes, a snapshot reports 0 events even
        # though the breaker is still open (it stays sticky).
        registry = CircuitBreakerRegistry(
            policy=CircuitBreakerPolicy(window_seconds=10.0, max_restarts=1)
        )
        registry.record_restart("eng-c", now=0.0)
        registry.record_restart("eng-c", now=1.0)
        self.assertTrue(registry.is_open("eng-c"))
        snap = registry.snapshot(now=1_000.0)
        self.assertEqual(snap["eng-c"].restart_count_in_window, 0)
        # Still open — sticky.
        self.assertTrue(snap["eng-c"].is_open)


if __name__ == "__main__":
    unittest.main()
