from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping, Optional, Sequence

from ._sqlite import SQLITE_WRITE_LOCK

DEFAULT_STALE_RETENTION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 30_000
_CLEANUP_INTERVAL_SECONDS = 5 * 60
_last_cleanup_monotonic = 0.0


@dataclass(frozen=True)
class LocalCacheEntry:
    namespace: str
    cache_key: str
    provider: str
    range_start: Optional[str]
    range_end: Optional[str]
    scope_hash: str
    payload: dict[str, Any]
    metadata: dict[str, Any]
    fetched_at: float
    expires_at: float
    last_accessed_at: float
    is_stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "cache_key": self.cache_key,
            "provider": self.provider,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "scope_hash": self.scope_hash,
            "metadata": self.metadata,
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
            "last_accessed_at": self.last_accessed_at,
            "is_stale": self.is_stale,
        }


def load_json_cache(
    namespace: str,
    cache_key: str,
    ttl_seconds: Optional[int] = None,
    allow_stale: bool = False,
    touch: bool = True,
) -> Optional[LocalCacheEntry]:
    db_path = _database_path()
    if not db_path.exists():
        return None

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            """
            SELECT
                namespace,
                cache_key,
                provider,
                range_start,
                range_end,
                scope_hash,
                payload_json,
                metadata_json,
                fetched_at,
                expires_at,
                last_accessed_at
            FROM local_cache_entries
            WHERE namespace = ? AND cache_key = ?
            """,
            (namespace, cache_key),
        ).fetchone()
        if row is None:
            return None

        now = time.time()
        fetched_at = float(row["fetched_at"])
        expires_at = float(row["expires_at"])
        expired = expires_at <= now
        ttl_expired = ttl_seconds is not None and ttl_seconds >= 0 and fetched_at + ttl_seconds <= now
        is_stale = expired or ttl_expired
        if is_stale and not allow_stale:
            return None

        payload = _deserialize_json_object(row["payload_json"])
        if payload is None:
            return None

        metadata = _deserialize_json_object(row["metadata_json"]) or {}
        last_accessed_at = float(row["last_accessed_at"])
        if touch:
            last_accessed_at = now
            connection.execute(
                """
                UPDATE local_cache_entries
                SET last_accessed_at = ?
                WHERE namespace = ? AND cache_key = ?
                """,
                (now, namespace, cache_key),
            )
        return LocalCacheEntry(
            namespace=row["namespace"],
            cache_key=row["cache_key"],
            provider=row["provider"],
            range_start=row["range_start"],
            range_end=row["range_end"],
            scope_hash=row["scope_hash"],
            payload=payload,
            metadata=metadata,
            fetched_at=fetched_at,
            expires_at=expires_at,
            last_accessed_at=last_accessed_at,
            is_stale=is_stale,
        )


def save_json_cache(
    namespace: str,
    cache_key: str,
    provider: str,
    range_start: Optional[str],
    range_end: Optional[str],
    scope_hash: str,
    ttl_seconds: int,
    payload: Mapping[str, Any],
    metadata: Optional[Mapping[str, Any]] = None,
    stale_retention_seconds: Optional[int] = DEFAULT_STALE_RETENTION_SECONDS,
) -> None:
    if ttl_seconds <= 0:
        return

    db_path = _database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    now = time.time()
    expires_at = now + ttl_seconds

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO local_cache_entries (
                namespace,
                cache_key,
                provider,
                range_start,
                range_end,
                scope_hash,
                payload_json,
                metadata_json,
                fetched_at,
                expires_at,
                last_accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace, cache_key) DO UPDATE SET
                provider = excluded.provider,
                range_start = excluded.range_start,
                range_end = excluded.range_end,
                scope_hash = excluded.scope_hash,
                payload_json = excluded.payload_json,
                metadata_json = excluded.metadata_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at,
                last_accessed_at = excluded.last_accessed_at
            """,
            (
                namespace,
                cache_key,
                provider,
                range_start,
                range_end,
                scope_hash,
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True),
                now,
                expires_at,
                now,
            ),
        )
        if _should_run_cleanup():
            cleanup_before = now
            if stale_retention_seconds is not None and stale_retention_seconds > 0:
                cleanup_before = now - stale_retention_seconds
            connection.execute(
                "DELETE FROM local_cache_entries WHERE expires_at <= ?",
                (cleanup_before,),
            )
            _mark_cleanup_ran()


def list_json_cache_entries(
    namespace: Optional[str] = None,
    provider: Optional[str] = None,
    include_expired: bool = True,
    limit: int = 100,
) -> list[LocalCacheEntry]:
    db_path = _database_path()
    if not db_path.exists():
        return []

    resolved_limit = max(1, limit)
    clauses = []
    params: list[Any] = []
    if namespace:
        clauses.append("namespace = ?")
        params.append(namespace)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if not include_expired:
        clauses.append("expires_at > ?")
        params.append(time.time())

    where_clause = ""
    if clauses:
        where_clause = "WHERE " + " AND ".join(clauses)

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""
            SELECT
                namespace,
                cache_key,
                provider,
                range_start,
                range_end,
                scope_hash,
                payload_json,
                metadata_json,
                fetched_at,
                expires_at,
                last_accessed_at
            FROM local_cache_entries
            {where_clause}
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (*params, resolved_limit),
        ).fetchall()

    now = time.time()
    entries = []
    for row in rows:
        payload = _deserialize_json_object(row["payload_json"]) or {}
        metadata = _deserialize_json_object(row["metadata_json"]) or {}
        expires_at = float(row["expires_at"])
        entries.append(
            LocalCacheEntry(
                namespace=row["namespace"],
                cache_key=row["cache_key"],
                provider=row["provider"],
                range_start=row["range_start"],
                range_end=row["range_end"],
                scope_hash=row["scope_hash"],
                payload=payload,
                metadata=metadata,
                fetched_at=float(row["fetched_at"]),
                expires_at=expires_at,
                last_accessed_at=float(row["last_accessed_at"]),
                is_stale=expires_at <= now,
            )
        )
    return entries


def cleanup_json_cache(
    namespace: Optional[str] = None,
    stale_retention_seconds: Optional[int] = DEFAULT_STALE_RETENTION_SECONDS,
) -> int:
    db_path = _database_path()
    if not db_path.exists():
        return 0

    threshold = time.time()
    if stale_retention_seconds is not None and stale_retention_seconds > 0:
        threshold = threshold - stale_retention_seconds

    clauses = ["expires_at <= ?"]
    params: list[Any] = [threshold]
    if namespace:
        clauses.append("namespace = ?")
        params.append(namespace)

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        cursor = connection.execute(
            f"DELETE FROM local_cache_entries WHERE {' AND '.join(clauses)}",
            params,
        )
        deleted_count = int(cursor.rowcount or 0)
        _mark_cleanup_ran()
    return deleted_count


def local_cache_database_path() -> Path:
    return _database_path()


def _should_run_cleanup() -> bool:
    now = time.monotonic()
    if _last_cleanup_monotonic == 0.0:
        return True
    return now - _last_cleanup_monotonic >= _CLEANUP_INTERVAL_SECONDS


def _mark_cleanup_ran() -> None:
    global _last_cleanup_monotonic
    _last_cleanup_monotonic = time.monotonic()


def _reset_cleanup_schedule_for_tests() -> None:
    global _last_cleanup_monotonic
    _last_cleanup_monotonic = 0.0


def _connect(db_path: Path) -> sqlite3.Connection:
    busy_timeout_ms = _sqlite_busy_timeout_ms()
    connection = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    _try_sqlite_pragma(connection, "PRAGMA journal_mode = WAL")
    _try_sqlite_pragma(connection, "PRAGMA synchronous = NORMAL")
    return connection


def _sqlite_busy_timeout_ms() -> int:
    configured_value = os.getenv("YULE_SQLITE_BUSY_TIMEOUT_MS")
    if configured_value and configured_value.strip():
        try:
            return max(1000, int(configured_value.strip()))
        except ValueError:
            return DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    return DEFAULT_SQLITE_BUSY_TIMEOUT_MS


def _try_sqlite_pragma(connection: sqlite3.Connection, statement: str) -> None:
    try:
        connection.execute(statement)
    except sqlite3.OperationalError:
        pass


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS local_cache_entries (
            namespace TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            provider TEXT NOT NULL,
            range_start TEXT,
            range_end TEXT,
            scope_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            metadata_json TEXT,
            fetched_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_accessed_at REAL NOT NULL,
            PRIMARY KEY(namespace, cache_key)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_local_cache_entries_expires_at
        ON local_cache_entries (expires_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_local_cache_entries_namespace_range
        ON local_cache_entries (namespace, range_start, range_end)
        """
    )


def _database_path() -> Path:
    configured_path = os.getenv("YULE_CACHE_DB_PATH")
    if configured_path and configured_path.strip():
        return Path(configured_path).expanduser()

    repo_root = os.getenv("YULE_REPO_ROOT")
    base_dir = Path(repo_root) if repo_root else Path.cwd()
    return base_dir / ".cache" / "yule" / "cache.sqlite3"


def _deserialize_json_object(value: Optional[str]) -> Optional[dict[str, Any]]:
    if value is None:
        return {}

    try:
        data = json.loads(value)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    return data
