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

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Tuple


logger = logging.getLogger(__name__)


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
    parent has its own view of the *event ledger*. The
    open-or-not bit + opened_at + last_reason is also mirrored
    into a small SQLite table when *persistence* is provided so
    a sibling process (e.g. ``yule runtime status --post-discord``)
    can see "X is circuit-open" without poking the supervisor's
    in-memory state. Persistence is opt-in so existing callers
    that don't need cross-process visibility stay zero-overhead.
    """

    def __init__(
        self,
        *,
        policy: Optional[CircuitBreakerPolicy] = None,
        persistence: Optional["CircuitBreakerPersistence"] = None,
        load_existing: bool = True,
    ) -> None:
        self._policy = policy or CircuitBreakerPolicy()
        self._states: dict[str, CircuitBreakerState] = {}
        self._persistence = persistence
        if persistence is not None and load_existing:
            try:
                for row in persistence.load_open_circuits():
                    state = CircuitBreakerState(service_id=row.service_id)
                    state.opened_at = row.opened_at
                    state.last_reason = row.last_reason
                    self._states[row.service_id] = state
            except Exception:  # noqa: BLE001 - persistence is observability
                logger.warning(
                    "circuit breaker persistence load raised; "
                    "starting with empty in-memory state",
                    exc_info=True,
                )

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
            self._persist_open(state)
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

    def reset(self, service_id: str) -> bool:
        """Drop all state for *service_id*. Operator hook for a
        manual recovery (``yule runtime status`` shows circuit-open;
        operator fixes config; restart of the supervisor parent
        clears state on its own).

        Returns True iff there was something to clear (in-memory or
        persisted). Lets the CLI tell the operator "no circuit was
        open for X" instead of silently succeeding.
        """

        had_in_memory = service_id in self._states
        self._states.pop(service_id, None)
        had_persisted = False
        if self._persistence is not None:
            try:
                had_persisted = self._persistence.clear(service_id)
            except Exception:  # noqa: BLE001 - persistence is best-effort
                logger.warning(
                    "circuit breaker persistence clear raised", exc_info=True
                )
        return had_in_memory or had_persisted

    def _persist_open(self, state: CircuitBreakerState) -> None:
        if self._persistence is None or state.opened_at is None:
            return
        try:
            self._persistence.mark_open(
                service_id=state.service_id,
                opened_at=state.opened_at,
                last_reason=state.last_reason or "",
            )
        except Exception:  # noqa: BLE001 - never let persistence crash record_restart
            logger.warning(
                "circuit breaker persistence mark_open raised", exc_info=True
            )

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


# ---------------------------------------------------------------------------
# Persistence — small SQLite table so a sibling process can see open circuits.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedCircuitRow:
    """One ``circuit_breaker_state`` row.

    Persistence captures only the open-or-not bit + when + why.
    The sliding-window event ledger stays in supervisor memory —
    persisting raw events would just bloat the cache for no
    operational benefit (the breaker is sticky once open).
    """

    service_id: str
    opened_at: float
    last_reason: str


class CircuitBreakerPersistence:
    """SQLite-backed mirror of open-circuit state.

    Stores only services whose breaker is currently open. ``reset``
    deletes the row; a normal close (which today only happens via
    operator reset) does the same. Schema lives in the same
    ``cache.sqlite3`` file as the queue store so a single backup
    captures everything.
    """

    def __init__(self, *, db_path: Optional[Path] = None) -> None:
        self._db_path = _resolve_circuit_db_path(db_path)
        with _connect(self._db_path) as conn:
            _ensure_circuit_schema(conn)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def mark_open(
        self, *, service_id: str, opened_at: float, last_reason: str
    ) -> None:
        if not service_id:
            return
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO circuit_breaker_state
                    (service_id, opened_at, last_reason)
                VALUES (?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    opened_at = excluded.opened_at,
                    last_reason = excluded.last_reason
                """,
                (service_id, float(opened_at), str(last_reason or "")),
            )

    def clear(self, service_id: str) -> bool:
        """Return True iff a row was actually deleted."""

        if not service_id:
            return False
        with _connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM circuit_breaker_state WHERE service_id = ?",
                (service_id,),
            )
            return cur.rowcount > 0

    def load_open_circuits(self) -> Tuple[PersistedCircuitRow, ...]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT service_id, opened_at, last_reason "
                "FROM circuit_breaker_state ORDER BY opened_at ASC"
            ).fetchall()
            return tuple(
                PersistedCircuitRow(
                    service_id=str(row["service_id"]),
                    opened_at=float(row["opened_at"]),
                    last_reason=str(row["last_reason"] or ""),
                )
                for row in rows
            )


def load_persisted_circuit_snapshots(
    *, persistence: Optional[CircuitBreakerPersistence] = None
) -> Mapping[str, CircuitSnapshot]:
    """Convenience for the status CLI / poster.

    Reads the persistence table and projects each open row into
    a :class:`CircuitSnapshot` (with ``is_open=True`` always —
    only open rows are persisted). ``restart_count_in_window`` is
    not known cross-process; we surface 0 so the markdown
    formatter still has a value to render.
    """

    store = persistence or CircuitBreakerPersistence()
    out: dict[str, CircuitSnapshot] = {}
    try:
        rows = store.load_open_circuits()
    except Exception:  # noqa: BLE001 - never let persistence crash status
        logger.warning(
            "circuit breaker persistence load raised in status path",
            exc_info=True,
        )
        return out
    for row in rows:
        out[row.service_id] = CircuitSnapshot(
            service_id=row.service_id,
            is_open=True,
            restart_count_in_window=0,
            opened_at=row.opened_at,
            last_reason=row.last_reason or None,
        )
    return out


# ---------------------------------------------------------------------------
# SQLite plumbing — kept private so callers go through the persistence class.
# ---------------------------------------------------------------------------


def _resolve_circuit_db_path(override: Optional[Path] = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    configured = os.getenv("YULE_CACHE_DB_PATH")
    if configured and configured.strip():
        return Path(configured).expanduser()
    repo_root = os.getenv("YULE_REPO_ROOT")
    base = Path(repo_root) if repo_root else Path.cwd()
    return base / ".cache" / "yule" / "cache.sqlite3"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _ensure_circuit_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS circuit_breaker_state (
            service_id  TEXT PRIMARY KEY,
            opened_at   REAL NOT NULL,
            last_reason TEXT NOT NULL DEFAULT ''
        );
        """
    )


__all__ = (
    "CircuitBreakerPersistence",
    "CircuitBreakerPolicy",
    "CircuitBreakerRegistry",
    "CircuitBreakerState",
    "CircuitSnapshot",
    "DEFAULT_CIRCUIT_MAX_RESTARTS",
    "DEFAULT_CIRCUIT_WINDOW_SECONDS",
    "PersistedCircuitRow",
    "load_persisted_circuit_snapshots",
)
