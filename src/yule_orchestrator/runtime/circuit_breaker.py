"""Per-service circuit breaker — A-M7.

The supervisor restart loop in :mod:`runtime.subprocess_supervisor`
will keep restarting a child indefinitely with backoff. That's
fine for a transient hiccup but bad for a hard config / code
error: an operator's terminal floods with restarts and the
``status`` page never settles. The breaker bounds the noise.

Policy (per spec):

  * 5 restarts within a 5-minute sliding window → circuit open
  * Once open, the supervisor must skip restart for that service
  * Reopening is operator-driven (process restart) — no
    auto-half-open path, since the failure is almost always a
    config / code bug that won't fix itself

Pure-Python and clock-injectable so tests don't need real time.
The supervisor wires it in by passing a registry to
:func:`run_runtime_up`. Without a registry, behaviour is
identical to A-M6.0 (no breaking change).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping, Optional


DEFAULT_CIRCUIT_WINDOW_SECONDS: float = 300.0
DEFAULT_CIRCUIT_MAX_RESTARTS: int = 5


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    """Threshold + window. ``max_restarts`` is the count that
    *trips* the breaker — i.e. the (max_restarts+1)-th restart in
    the window opens the circuit. Default tuned for the spec's
    "5 restarts in 5 minutes" rule.
    """

    window_seconds: float = DEFAULT_CIRCUIT_WINDOW_SECONDS
    max_restarts: int = DEFAULT_CIRCUIT_MAX_RESTARTS


@dataclass
class CircuitBreakerState:
    """Per-service ledger.

    ``restart_events`` is the wall-clock list of restart attempts
    inside the current window. ``opened_at`` is set the moment the
    breaker trips and stays set until :meth:`reset` is called by an
    operator (process restart). The state is NOT persisted across
    supervisor restarts — the breaker is in-process safety.
    """

    service_id: str
    restart_events: list[float] = field(default_factory=list)
    opened_at: Optional[float] = None
    last_reason: Optional[str] = None

    def event_count_in_window(
        self, *, now: float, window_seconds: float
    ) -> int:
        cutoff = now - max(0.0, float(window_seconds))
        return sum(1 for t in self.restart_events if t >= cutoff)


@dataclass(frozen=True)
class CircuitSnapshot:
    """Read-only view consumed by status / markdown surfaces."""

    service_id: str
    is_open: bool
    restart_count_in_window: int
    opened_at: Optional[float]
    last_reason: Optional[str]


class CircuitBreakerRegistry:
    """Owns per-service :class:`CircuitBreakerState`.

    One registry instance lives in the supervisor parent for the
    duration of ``yule runtime up``. Workers don't share — each
    parent has its own view.
    """

    def __init__(
        self, *, policy: Optional[CircuitBreakerPolicy] = None
    ) -> None:
        self._policy = policy or CircuitBreakerPolicy()
        self._states: dict[str, CircuitBreakerState] = {}

    @property
    def policy(self) -> CircuitBreakerPolicy:
        return self._policy

    def state(self, service_id: str) -> CircuitBreakerState:
        existing = self._states.get(service_id)
        if existing is not None:
            return existing
        fresh = CircuitBreakerState(service_id=service_id)
        self._states[service_id] = fresh
        return fresh

    def record_restart(
        self,
        service_id: str,
        *,
        now: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> CircuitBreakerState:
        """Append a restart event and return the updated state.

        Side effect: trims events older than the window so the
        ledger doesn't grow unbounded for a long-running service
        that stays healthy after a brief flap.
        """

        now_ts = now if now is not None else time.time()
        state = self.state(service_id)
        cutoff = now_ts - self._policy.window_seconds
        # Drop expired events; the breaker is a sliding window so a
        # service that flapped an hour ago doesn't keep the count
        # inflated forever.
        state.restart_events = [
            t for t in state.restart_events if t >= cutoff
        ]
        state.restart_events.append(now_ts)
        if (
            state.opened_at is None
            and len(state.restart_events) > self._policy.max_restarts
        ):
            state.opened_at = now_ts
            state.last_reason = (
                reason
                or (
                    f"{len(state.restart_events)} restarts within "
                    f"{int(self._policy.window_seconds)}s window"
                )
            )
        return state

    def is_open(
        self, service_id: str, *, now: Optional[float] = None
    ) -> bool:
        """Whether the breaker is currently open.

        ``now`` is accepted for symmetry with :meth:`record_restart`
        even though "open" today is a sticky bool — keeps the API
        shape stable if a future M7.x adds time-based half-open.
        """

        state = self._states.get(service_id)
        if state is None:
            return False
        return state.opened_at is not None

    def reset(self, service_id: str) -> None:
        """Drop all state for *service_id*. Operator hook for a
        manual recovery (``yule runtime status`` shows circuit-open;
        operator fixes config; restart of the supervisor parent
        clears state on its own).
        """

        self._states.pop(service_id, None)

    def snapshot(
        self, *, now: Optional[float] = None
    ) -> Mapping[str, CircuitSnapshot]:
        """Read-only summary keyed by service_id.

        Consumed by the status renderer (M6.3) + markdown formatter
        so the operator can see "X is circuit-open" without reaching
        into the registry's internals.
        """

        now_ts = now if now is not None else time.time()
        out: dict[str, CircuitSnapshot] = {}
        for service_id, state in self._states.items():
            out[service_id] = CircuitSnapshot(
                service_id=service_id,
                is_open=state.opened_at is not None,
                restart_count_in_window=state.event_count_in_window(
                    now=now_ts,
                    window_seconds=self._policy.window_seconds,
                ),
                opened_at=state.opened_at,
                last_reason=state.last_reason,
            )
        return out


__all__ = (
    "CircuitBreakerPolicy",
    "CircuitBreakerRegistry",
    "CircuitBreakerState",
    "CircuitSnapshot",
    "DEFAULT_CIRCUIT_MAX_RESTARTS",
    "DEFAULT_CIRCUIT_WINDOW_SECONDS",
)
