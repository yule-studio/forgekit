"""Process heartbeat + lease watchdog primitives.

A-M2 lays the second piece of the always-on runtime: every long-running
process (gateway, member bot, research worker, obsidian writer) emits
a periodic heartbeat into the shared SQLite, and the supervisor reads
those heartbeats to decide which services are alive. Combined with
:meth:`agents.job_queue.store.JobQueue.reap_expired_leases`, this lets
the supervisor recover dead-process work without restarting the whole
fleet.

Design choices kept conservative on purpose:
- Same SQLite file as ``job_queue`` so a single transaction can move
  a stuck job *and* mark its worker dead.
- Heartbeats are upsert-by-service_id, so we keep the latest beat
  only — diagnostic surfaces never need history-light heartbeat
  records, and small tables stay fast under WAL.
- ``check_liveness`` is pure read + clock comparison; no side effects.
  The actual restart action is the operator's (systemd) job — this
  module just tells the supervisor *which* services are stale.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from ...storage._sqlite import SQLITE_WRITE_LOCK


DEFAULT_HEARTBEAT_INTERVAL_SECONDS: float = 30.0
#: 누락 임계 — interval 의 3 배 가까이 빠지면 dead 로 본다. 짧은 GC
#: 일시중단 / 디스크 IO 대기 / 짧은 네트워크 stall 정도는 한 번만
#: 빠진다고 죽이지 않게 보수적으로 잡는다.
DEFAULT_HEARTBEAT_DEADLINE_SECONDS: float = 90.0


def _resolve_db_path(override: Optional[Path] = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    configured = os.getenv("YULE_CACHE_DB_PATH")
    if configured and configured.strip():
        return Path(configured).expanduser()
    repo_root = os.getenv("YULE_REPO_ROOT")
    base = Path(repo_root) if repo_root else Path.cwd()
    return base / ".cache" / "yule" / "cache.sqlite3"


def _busy_timeout_ms() -> int:
    raw = os.getenv("YULE_SQLITE_BUSY_TIMEOUT_MS")
    if raw and raw.strip():
        try:
            return max(1000, int(raw.strip()))
        except ValueError:
            return 30000
    return 30000


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = _busy_timeout_ms()
    conn = sqlite3.connect(db_path, timeout=timeout_ms / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS service_heartbeats (
            service_id     TEXT PRIMARY KEY,
            last_beat      REAL NOT NULL,
            pid            INTEGER,
            metadata_json  TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_service_heartbeats_last_beat
            ON service_heartbeats (last_beat);
        """
    )


@dataclass(frozen=True)
class HeartbeatRecord:
    """One ``service_heartbeats`` row.

    ``last_beat`` is wall-clock (``time.time()``), so callers can
    compare against ``time.time()`` directly without timezone juggling.
    """

    service_id: str
    last_beat: float
    pid: Optional[int]
    metadata: Mapping[str, Any]

    def is_alive(self, *, now: Optional[float] = None, deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS) -> bool:
        now_ts = now if now is not None else time.time()
        return (now_ts - self.last_beat) <= max(1.0, float(deadline_seconds))


class HeartbeatStore:
    """Facade over the ``service_heartbeats`` table.

    Each long-running service holds one instance and calls
    :meth:`record` from its event loop every
    :data:`DEFAULT_HEARTBEAT_INTERVAL_SECONDS`. The supervisor holds
    a separate instance and calls :meth:`stale_services` in its watch
    loop to find services that haven't beat recently.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = _resolve_db_path(db_path)
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            _ensure_schema(conn)

    def record(
        self,
        service_id: str,
        *,
        pid: Optional[int] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> HeartbeatRecord:
        if not service_id:
            raise ValueError("service_id is required")
        now_ts = now if now is not None else time.time()
        import json

        payload = json.dumps(dict(metadata or {}), ensure_ascii=False)
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO service_heartbeats (service_id, last_beat, pid, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    last_beat = excluded.last_beat,
                    pid       = excluded.pid,
                    metadata_json = excluded.metadata_json
                """,
                (service_id, now_ts, pid, payload),
            )
        return HeartbeatRecord(
            service_id=service_id,
            last_beat=now_ts,
            pid=pid,
            metadata=dict(metadata or {}),
        )

    def get(self, service_id: str) -> Optional[HeartbeatRecord]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM service_heartbeats WHERE service_id = ?",
                (service_id,),
            ).fetchone()
            return _row_to_record(row) if row is not None else None

    def list_all(self) -> Tuple[HeartbeatRecord, ...]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM service_heartbeats ORDER BY service_id ASC"
            ).fetchall()
            return tuple(_row_to_record(row) for row in rows)

    def stale_services(
        self,
        *,
        deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
        now: Optional[float] = None,
    ) -> Tuple[HeartbeatRecord, ...]:
        """Return every recorded service whose last beat is older than
        *deadline_seconds*. The supervisor uses this list to log dead
        services into ``#봇-상태`` and decide whether to take action.

        We do NOT treat "service never beat at all" as stale — only
        services that registered at least once and then went quiet.
        That avoids false positives for services intentionally
        disabled (e.g. frontend-engineer in the Spring-only stage).
        """

        now_ts = now if now is not None else time.time()
        cutoff = now_ts - max(1.0, float(deadline_seconds))
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM service_heartbeats WHERE last_beat <= ? ORDER BY last_beat ASC",
                (cutoff,),
            ).fetchall()
            return tuple(_row_to_record(row) for row in rows)

    def clear(self, service_id: str) -> None:
        """Drop a heartbeat row entirely.

        Used when a service is being intentionally retired (e.g. the
        operator stopped a member bot for the day) so the supervisor
        diagnostic doesn't keep flagging the gone-on-purpose service
        as dead forever.
        """

        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM service_heartbeats WHERE service_id = ?",
                (service_id,),
            )


def _row_to_record(row: sqlite3.Row) -> HeartbeatRecord:
    import json

    raw_meta = row["metadata_json"] or "{}"
    try:
        metadata = json.loads(raw_meta)
        if not isinstance(metadata, dict):
            metadata = {}
    except Exception:  # noqa: BLE001 - never let a bad row crash the supervisor
        metadata = {}
    return HeartbeatRecord(
        service_id=row["service_id"],
        last_beat=float(row["last_beat"]),
        pid=int(row["pid"]) if row["pid"] is not None else None,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Supervisor sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupervisorSweepReport:
    """Result of one watchdog cycle.

    ``stale`` lists services that haven't beat recently — the operator
    decides whether to restart them via systemd.
    ``reaped_jobs`` is the count of jobs whose lease expired and were
    moved to ``failed_retryable`` so the next worker can retry.
    """

    stale: Tuple[HeartbeatRecord, ...]
    reaped_jobs: int
    swept_at: float


def run_supervisor_sweep(
    *,
    heartbeat_store: HeartbeatStore,
    job_queue: Any,
    deadline_seconds: float = DEFAULT_HEARTBEAT_DEADLINE_SECONDS,
    now: Optional[float] = None,
) -> SupervisorSweepReport:
    """One watchdog tick.

    Composes :meth:`HeartbeatStore.stale_services` with
    :meth:`JobQueue.reap_expired_leases`. The supervisor's long-running
    loop calls this on a timer (default every 5 s) — the function is
    pure-Python so it's directly testable without running threads.

    *job_queue* is typed loosely (``Any``) so this module doesn't
    introduce a circular import with :mod:`agents.job_queue.store`.
    """

    now_ts = now if now is not None else time.time()
    stale = heartbeat_store.stale_services(
        deadline_seconds=deadline_seconds, now=now_ts
    )
    reaped = job_queue.reap_expired_leases(now=now_ts)
    return SupervisorSweepReport(
        stale=stale,
        reaped_jobs=len(reaped),
        swept_at=now_ts,
    )


__all__ = (
    "DEFAULT_HEARTBEAT_DEADLINE_SECONDS",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "HeartbeatRecord",
    "HeartbeatStore",
    "SupervisorSweepReport",
    "run_supervisor_sweep",
)
