"""SQLite-backed persistence for the job queue.

This module is the only place that talks to the ``job_queue`` /
``job_dependencies`` tables. Workers / dispatchers / supervisor
should call :class:`JobQueue` methods rather than running their own
SQL — that way the lease + transition contract stays in one place.

Concurrency model:
- One SQLite file (default ``.cache/yule/cache.sqlite3``) shared
  across processes via WAL + ``busy_timeout``. Same convention as
  :mod:`storage.local_cache`.
- Each :class:`JobQueue` method opens a short-lived connection,
  begins a transaction, and commits before returning. ``pick`` uses
  ``BEGIN IMMEDIATE`` so two workers can't claim the same row.
- :data:`SQLITE_WRITE_LOCK` (a process-local RLock) protects
  in-process callers from interleaving — cross-process safety still
  comes from SQLite's file lock + WAL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ...storage._sqlite import SQLITE_WRITE_LOCK
from .state_machine import JobState, validate_transition


DEFAULT_LEASE_SECONDS: float = 60.0
DEFAULT_BUSY_TIMEOUT_MS: int = 30000


class JobQueueError(RuntimeError):
    """Raised for queue logic errors (bad transition, missing job, …)."""


class QueueDatabaseError(RuntimeError):
    """Raised when the SQLite layer itself fails (corrupt file, perms, …)."""


@dataclass(frozen=True)
class Job:
    """Single ``job_queue`` row mirrored as a frozen dataclass.

    Times are wall-clock floats (``time.time()``) so they round-trip
    through SQLite REAL columns without timezone juggling. Convert
    to :class:`datetime` at the display boundary, not here.
    """

    job_id: str
    session_id: str
    job_type: str
    state: JobState
    payload: Mapping[str, Any] = field(default_factory=dict)
    result: Mapping[str, Any] = field(default_factory=dict)
    role: Optional[str] = None
    priority: int = 0
    attempt: int = 0
    max_attempts: int = 3
    available_at: float = 0.0
    picked_by: Optional[str] = None
    picked_until: Optional[float] = None
    created_at: float = 0.0
    updated_at: float = 0.0


def _utc_now() -> float:
    return time.time()


def _new_job_id() -> str:
    """ulid-ish identifier — sortable timestamp prefix + random tail."""

    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


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
            return DEFAULT_BUSY_TIMEOUT_MS
    return DEFAULT_BUSY_TIMEOUT_MS


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
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_queue (
            job_id        TEXT PRIMARY KEY,
            session_id    TEXT NOT NULL,
            role          TEXT,
            job_type      TEXT NOT NULL,
            state         TEXT NOT NULL,
            priority      INTEGER NOT NULL DEFAULT 0,
            payload_json  TEXT NOT NULL DEFAULT '{}',
            result_json   TEXT NOT NULL DEFAULT '{}',
            attempt       INTEGER NOT NULL DEFAULT 0,
            max_attempts  INTEGER NOT NULL DEFAULT 3,
            available_at  REAL NOT NULL,
            picked_by     TEXT,
            picked_until  REAL,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_job_queue_dispatch
            ON job_queue (state, available_at, priority DESC);
        CREATE INDEX IF NOT EXISTS idx_job_queue_session
            ON job_queue (session_id);

        CREATE TABLE IF NOT EXISTS job_dependencies (
            job_id    TEXT NOT NULL,
            parent_id TEXT NOT NULL,
            PRIMARY KEY (job_id, parent_id),
            FOREIGN KEY (job_id) REFERENCES job_queue(job_id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES job_queue(job_id) ON DELETE CASCADE
        );
        """
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        session_id=row["session_id"],
        role=row["role"],
        job_type=row["job_type"],
        state=JobState(row["state"]),
        priority=int(row["priority"]),
        payload=json.loads(row["payload_json"] or "{}"),
        result=json.loads(row["result_json"] or "{}"),
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        available_at=float(row["available_at"]),
        picked_by=row["picked_by"],
        picked_until=(
            float(row["picked_until"]) if row["picked_until"] is not None else None
        ),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class JobQueue:
    """Facade over the ``job_queue`` + ``job_dependencies`` tables."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = _resolve_db_path(db_path)
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            _ensure_schema(conn)

    # ------------------------------------------------------------------
    # Producer side
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        session_id: str,
        job_type: str,
        role: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: Optional[float] = None,
        after_jobs: Sequence[str] = (),
        now: Optional[float] = None,
    ) -> Job:
        """Insert a new job. Default state is ``queued`` unless
        *after_jobs* is non-empty, in which case the job lands in
        ``waiting_for_role`` until every parent reaches a terminal
        success state (``saved``).
        """

        if not session_id:
            raise JobQueueError("session_id is required")
        if not job_type:
            raise JobQueueError("job_type is required")
        if max_attempts < 1:
            raise JobQueueError("max_attempts must be >= 1")

        now_ts = now if now is not None else _utc_now()
        ready_at = available_at if available_at is not None else now_ts
        initial_state = (
            JobState.WAITING_FOR_ROLE if after_jobs else JobState.QUEUED
        )
        job = Job(
            job_id=_new_job_id(),
            session_id=session_id,
            role=role,
            job_type=job_type,
            state=initial_state,
            priority=int(priority),
            payload=dict(payload or {}),
            attempt=0,
            max_attempts=int(max_attempts),
            available_at=ready_at,
            picked_by=None,
            picked_until=None,
            created_at=now_ts,
            updated_at=now_ts,
        )
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO job_queue (
                    job_id, session_id, role, job_type, state, priority,
                    payload_json, result_json, attempt, max_attempts,
                    available_at, picked_by, picked_until,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.session_id,
                    job.role,
                    job.job_type,
                    job.state.value,
                    job.priority,
                    json.dumps(job.payload, ensure_ascii=False),
                    json.dumps(dict(job.result), ensure_ascii=False),
                    job.attempt,
                    job.max_attempts,
                    job.available_at,
                    job.picked_by,
                    job.picked_until,
                    job.created_at,
                    job.updated_at,
                ),
            )
            for parent_id in after_jobs:
                if not parent_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO job_dependencies (job_id, parent_id) VALUES (?, ?)",
                    (job.job_id, parent_id),
                )
        return job

    def enqueue_fanout(
        self,
        *,
        session_id: str,
        job_type: str,
        roles: Iterable[str],
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        after_jobs: Sequence[str] = (),
        now: Optional[float] = None,
    ) -> Tuple[Job, ...]:
        """Convenience for the 'tech-lead picks N roles' pattern.

        Each role gets its own ``Job`` with shared payload + same
        parent dependencies, so the supervisor can fan-in via
        :meth:`children_of_session` later.
        """

        produced: list[Job] = []
        for role in roles:
            produced.append(
                self.enqueue(
                    session_id=session_id,
                    job_type=job_type,
                    role=role,
                    payload=payload,
                    priority=priority,
                    after_jobs=after_jobs,
                    now=now,
                )
            )
        return tuple(produced)

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    def pick(
        self,
        *,
        worker_id: str,
        job_types: Sequence[str] = (),
        roles: Sequence[str] = (),
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        now: Optional[float] = None,
    ) -> Optional[Job]:
        """Atomically claim the next eligible job for *worker_id*.

        Filters: ``state='queued'`` with ``available_at <= now`` and
        no unsatisfied dependencies. Optional *job_types* / *roles*
        narrow the candidate pool so a role worker only sees its own
        rows. Returns ``None`` when nothing is eligible.
        """

        if not worker_id:
            raise JobQueueError("worker_id is required")
        now_ts = now if now is not None else _utc_now()
        until_ts = now_ts + max(1.0, float(lease_seconds))

        type_clause = ""
        params: list[Any] = [JobState.QUEUED.value, now_ts]
        if job_types:
            placeholders = ",".join("?" for _ in job_types)
            type_clause = f" AND job_type IN ({placeholders})"
            params.extend(job_types)
        role_clause = ""
        if roles:
            placeholders = ",".join("?" for _ in roles)
            role_clause = f" AND role IN ({placeholders})"
            params.extend(roles)

        select_sql = f"""
            SELECT * FROM job_queue
            WHERE state = ?
              AND available_at <= ?
              {type_clause}
              {role_clause}
              AND NOT EXISTS (
                SELECT 1 FROM job_dependencies dep
                JOIN job_queue parent ON parent.job_id = dep.parent_id
                WHERE dep.job_id = job_queue.job_id
                  AND parent.state != 'saved'
              )
            ORDER BY priority DESC, available_at ASC, created_at ASC
            LIMIT 1
        """

        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise QueueDatabaseError(str(exc)) from exc
            row = conn.execute(select_sql, params).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job = _row_to_job(row)
            new_state = JobState.ASSIGNED
            validate_transition(job.state, new_state)
            conn.execute(
                """
                UPDATE job_queue
                SET state = ?,
                    picked_by = ?,
                    picked_until = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (new_state.value, worker_id, until_ts, now_ts, job.job_id),
            )
            conn.execute("COMMIT")
            return replace(
                job,
                state=new_state,
                picked_by=worker_id,
                picked_until=until_ts,
                updated_at=now_ts,
            )

    def transition(
        self,
        job_id: str,
        target: JobState,
        *,
        result: Optional[Mapping[str, Any]] = None,
        available_at: Optional[float] = None,
        clear_lease: bool = False,
        bump_attempt: bool = False,
        now: Optional[float] = None,
    ) -> Job:
        """Move *job_id* to *target* if the transition is allowed.

        ``clear_lease=True`` blanks ``picked_by`` / ``picked_until``
        so the job can be picked up again (used on retryable failures
        and explicit requeue). ``bump_attempt=True`` increments the
        attempt counter (used right before requeuing on
        ``failed_retryable`` transitions).
        """

        now_ts = now if now is not None else _utc_now()
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM job_queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise JobQueueError(f"unknown job_id: {job_id}")
            current = _row_to_job(row)
            validate_transition(current.state, target)

            new_attempt = current.attempt + 1 if bump_attempt else current.attempt
            new_picked_by = None if clear_lease else current.picked_by
            new_picked_until = None if clear_lease else current.picked_until
            new_available_at = (
                available_at if available_at is not None else current.available_at
            )
            merged_result: Mapping[str, Any] = current.result
            if result is not None:
                merged = dict(current.result)
                merged.update(result)
                merged_result = merged

            conn.execute(
                """
                UPDATE job_queue
                SET state = ?,
                    result_json = ?,
                    attempt = ?,
                    available_at = ?,
                    picked_by = ?,
                    picked_until = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    target.value,
                    json.dumps(dict(merged_result), ensure_ascii=False),
                    new_attempt,
                    new_available_at,
                    new_picked_by,
                    new_picked_until,
                    now_ts,
                    job_id,
                ),
            )
            conn.execute("COMMIT")

        return replace(
            current,
            state=target,
            result=dict(merged_result),
            attempt=new_attempt,
            available_at=new_available_at,
            picked_by=new_picked_by,
            picked_until=new_picked_until,
            updated_at=now_ts,
        )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM job_queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            return _row_to_job(row) if row is not None else None

    def list_for_session(
        self, session_id: str, *, states: Sequence[JobState] = ()
    ) -> Tuple[Job, ...]:
        sql = "SELECT * FROM job_queue WHERE session_id = ?"
        params: list[Any] = [session_id]
        if states:
            placeholders = ",".join("?" for _ in states)
            sql += f" AND state IN ({placeholders})"
            params.extend(s.value for s in states)
        sql += " ORDER BY created_at ASC"
        with _connect(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return tuple(_row_to_job(row) for row in rows)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def reap_expired_leases(
        self,
        *,
        now: Optional[float] = None,
    ) -> Tuple[Job, ...]:
        """Return jobs whose ``picked_until`` has passed and bounce
        them back to ``failed_retryable`` so the next worker can
        retry. Used by the supervisor watchdog.

        We bump ``attempt`` only when the job re-queues from
        ``failed_retryable`` to ``queued`` — that happens later in
        :meth:`requeue_retryable`. Reaping itself just records the
        timeout transition.
        """

        now_ts = now if now is not None else _utc_now()
        moved: list[Job] = []
        with SQLITE_WRITE_LOCK, _connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM job_queue
                WHERE state IN ('assigned', 'in_progress', 'researching', 'ready_for_obsidian')
                  AND picked_until IS NOT NULL
                  AND picked_until <= ?
                """,
                (now_ts,),
            ).fetchall()
            for row in rows:
                job = _row_to_job(row)
                target = JobState.FAILED_RETRYABLE
                try:
                    validate_transition(job.state, target)
                except ValueError:
                    # Skip silently — the supervisor reaper isn't the
                    # right place to fail loud, the next sweep will
                    # try again with up-to-date state.
                    continue
                conn.execute(
                    """
                    UPDATE job_queue
                    SET state = ?,
                        picked_by = NULL,
                        picked_until = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (target.value, now_ts, job.job_id),
                )
                moved.append(
                    replace(
                        job,
                        state=target,
                        picked_by=None,
                        picked_until=None,
                        updated_at=now_ts,
                    )
                )
            conn.execute("COMMIT")
        return tuple(moved)

    def requeue_retryable(
        self,
        job_id: str,
        *,
        backoff_seconds: float = 5.0,
        now: Optional[float] = None,
    ) -> Job:
        """Move a ``failed_retryable`` job back to ``queued`` with
        exponential backoff. Increments ``attempt``. Caps at
        ``max_attempts`` — beyond that, the job is moved to
        ``failed_terminal`` instead.
        """

        now_ts = now if now is not None else _utc_now()
        current = self.get(job_id)
        if current is None:
            raise JobQueueError(f"unknown job_id: {job_id}")
        if current.state != JobState.FAILED_RETRYABLE:
            raise JobQueueError(
                f"requeue_retryable expects failed_retryable, got {current.state.value}"
            )
        next_attempt = current.attempt + 1
        if next_attempt >= current.max_attempts:
            return self.transition(
                job_id,
                JobState.FAILED_TERMINAL,
                clear_lease=True,
                now=now_ts,
            )
        return self.transition(
            job_id,
            JobState.QUEUED,
            available_at=now_ts + max(0.0, float(backoff_seconds)),
            clear_lease=True,
            bump_attempt=True,
            now=now_ts,
        )
