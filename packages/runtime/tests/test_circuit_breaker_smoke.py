"""Smoke tests for the moved circuit-breaker primitive.

These assert the open/closed transition semantics through the
``yule_runtime`` import path and confirm the legacy
``yule_engineering.runtime`` shim resolves to the *same* objects.
"""

from yule_runtime.circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitBreakerRegistry,
)


def test_breaker_opens_after_exceeding_max_restarts():
    registry = CircuitBreakerRegistry(
        policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=2)
    )
    base = 1_000.0

    # Up to and including max_restarts the breaker stays closed.
    registry.record_restart("svc", now=base + 0.0)
    registry.record_restart("svc", now=base + 1.0)
    assert registry.is_open("svc") is False

    # The (max_restarts + 1)-th restart trips it.
    registry.record_restart("svc", now=base + 2.0, reason="flap")
    assert registry.is_open("svc") is True

    snap = registry.snapshot(now=base + 2.0)["svc"]
    assert snap.is_open is True
    assert snap.last_reason == "flap"
    assert snap.restart_count_in_window == 3


def test_old_events_age_out_of_window():
    registry = CircuitBreakerRegistry(
        policy=CircuitBreakerPolicy(window_seconds=10.0, max_restarts=2)
    )
    # Two restarts long ago, then two fresh — the old ones fall out
    # of the sliding window so the breaker stays closed.
    registry.record_restart("svc", now=0.0)
    registry.record_restart("svc", now=1.0)
    registry.record_restart("svc", now=100.0)
    registry.record_restart("svc", now=101.0)
    assert registry.is_open("svc") is False


def test_reset_clears_state():
    registry = CircuitBreakerRegistry(
        policy=CircuitBreakerPolicy(window_seconds=60.0, max_restarts=0)
    )
    registry.record_restart("svc", now=0.0)
    assert registry.is_open("svc") is True
    assert registry.reset("svc") is True
    assert registry.is_open("svc") is False
    # Nothing left to clear the second time.
    assert registry.reset("svc") is False


def test_legacy_shim_is_same_module_object():
    import yule_engineering.runtime.circuit_breaker as shim
    import yule_runtime.circuit_breaker as real

    assert shim is real
    assert shim.CircuitBreakerRegistry is real.CircuitBreakerRegistry
