from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING, Any, Iterable, Optional

from ._sqlite import SQLITE_WRITE_LOCK

if TYPE_CHECKING:
    from yule_integrations.calendar.models import CalendarEvent, CalendarQueryResult, CalendarTodo

DEFAULT_CALENDAR_STATE_RETENTION_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 30_000
_RANGE_END_SENTINEL = f"{date.max.isoformat()}T23:59:59.999999+00:00"


@dataclass(frozen=True)
class CalendarStateRecord:
    source: str
    scope_hash: str
    item_type: str
    item_key: str
    external_uid: Optional[str]
    calendar_name: str
    title: str
    start_at: Optional[str]
    end_at: Optional[str]
    due_at: Optional[str]
    all_day: bool
    status: Optional[str]
    completed: bool
    completed_at: Optional[str]
    priority: Optional[int]
    percent_complete: Optional[int]
    description: str
    last_modified: Optional[str]
    category_color: Optional[str]
    payload: dict[str, Any]
    first_seen_at: float
    last_seen_at: float
    last_changed_at: float

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "scope_hash": self.scope_hash,
            "item_type": self.item_type,
            "item_key": self.item_key,
            "external_uid": self.external_uid,
            "calendar_name": self.calendar_name,
            "title": self.title,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "due_at": self.due_at,
            "all_day": self.all_day,
            "status": self.status,
            "completed": self.completed,
            "completed_at": self.completed_at,
            "priority": self.priority,
            "percent_complete": self.percent_complete,
            "description": self.description,
            "last_modified": self.last_modified,
            "category_color": self.category_color,
            "payload": self.payload,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "last_changed_at": self.last_changed_at,
        }


@dataclass(frozen=True)
class CalendarStateSyncSummary:
    inserted_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    completed_transition_count: int = 0

    def to_dict(self) -> dict:
        return {
            "inserted_count": self.inserted_count,
            "updated_count": self.updated_count,
            "unchanged_count": self.unchanged_count,
            "completed_transition_count": self.completed_transition_count,
        }


def sync_calendar_query_result(
    result: CalendarQueryResult,
    scope_hash: str,
) -> CalendarStateSyncSummary:
    items = [*_iter_event_records(result.events), *_iter_todo_records(result.todos)]
    if not items:
        return CalendarStateSyncSummary()

    db_path = _database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()

    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    completed_transition_count = 0

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)

        for item in items:
            row = connection.execute(
                """
                SELECT state_hash, completed, first_seen_at, last_changed_at
                FROM calendar_item_states
                WHERE source = ? AND item_type = ? AND item_key = ?
                """,
                (item["source"], item["item_type"], item["item_key"]),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO calendar_item_states (
                        source,
                        scope_hash,
                        item_type,
                        item_key,
                        external_uid,
                        calendar_name,
                        title,
                        start_at,
                        end_at,
                        due_at,
                        all_day,
                        status,
                        completed,
                        completed_at,
                        priority,
                        percent_complete,
                        description,
                        last_modified,
                        category_color,
                        state_hash,
                        payload_json,
                        first_seen_at,
                        last_seen_at,
                        last_changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["source"],
                        scope_hash,
                        item["item_type"],
                        item["item_key"],
                        item["external_uid"],
                        item["calendar_name"],
                        item["title"],
                        item["start_at"],
                        item["end_at"],
                        item["due_at"],
                        int(item["all_day"]),
                        item["status"],
                        int(item["completed"]),
                        item["completed_at"],
                        item["priority"],
                        item["percent_complete"],
                        item["description"],
                        item["last_modified"],
                        item["category_color"],
                        item["state_hash"],
                        item["payload_json"],
                        now,
                        now,
                        now,
                    ),
                )
                inserted_count += 1
                continue

            previous_completed = bool(row["completed"])
            state_changed = row["state_hash"] != item["state_hash"]
            first_seen_at = float(row["first_seen_at"])
            previous_last_changed_at = float(row["last_changed_at"])
            last_changed_at = now if state_changed else previous_last_changed_at

            connection.execute(
                """
                UPDATE calendar_item_states
                SET
                    scope_hash = ?,
                    external_uid = ?,
                    calendar_name = ?,
                    title = ?,
                    start_at = ?,
                    end_at = ?,
                    due_at = ?,
                    all_day = ?,
                    status = ?,
                    completed = ?,
                    completed_at = ?,
                    priority = ?,
                    percent_complete = ?,
                    description = ?,
                    last_modified = ?,
                    category_color = ?,
                    state_hash = ?,
                    payload_json = ?,
                    last_seen_at = ?,
                    last_changed_at = ?
                WHERE source = ? AND item_type = ? AND item_key = ?
                """,
                (
                    scope_hash,
                    item["external_uid"],
                    item["calendar_name"],
                    item["title"],
                    item["start_at"],
                    item["end_at"],
                    item["due_at"],
                    int(item["all_day"]),
                    item["status"],
                    int(item["completed"]),
                    item["completed_at"],
                    item["priority"],
                    item["percent_complete"],
                    item["description"],
                    item["last_modified"],
                    item["category_color"],
                    item["state_hash"],
                    item["payload_json"],
                    now,
                    last_changed_at,
                    item["source"],
                    item["item_type"],
                    item["item_key"],
                ),
            )
            if state_changed:
                updated_count += 1
                if item["item_type"] == "todo" and not previous_completed and item["completed"]:
                    completed_transition_count += 1
            else:
                unchanged_count += 1

    return CalendarStateSyncSummary(
        inserted_count=inserted_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        completed_transition_count=completed_transition_count,
    )


def list_calendar_state_records(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    include_completed: bool = True,
) -> list[CalendarStateRecord]:
    db_path = _database_path()
    if not db_path.exists():
        return []

    clauses: list[str] = []
    params: list[Any] = []

    if start_date is not None or end_date is not None:
        range_start_iso = (start_date or date.min).isoformat()
        resolved_end = end_date or date.max
        if resolved_end < date.max:
            range_end_exclusive_iso = (resolved_end + timedelta(days=1)).isoformat()
        else:
            range_end_exclusive_iso = _RANGE_END_SENTINEL
        or_parts: list[str] = []
        for column in ("start_at", "end_at", "due_at", "completed_at"):
            or_parts.append(f"({column} IS NOT NULL AND {column} >= ? AND {column} < ?)")
            params.extend([range_start_iso, range_end_exclusive_iso])
        clauses.append("(" + " OR ".join(or_parts) + ")")

    if not include_completed:
        clauses.append("completed = 0")

    where_clause = ""
    if clauses:
        where_clause = "WHERE " + " AND ".join(clauses)

    query = f"""
        SELECT
            source,
            scope_hash,
            item_type,
            item_key,
            external_uid,
            calendar_name,
            title,
            start_at,
            end_at,
            due_at,
            all_day,
            status,
            completed,
            completed_at,
            priority,
            percent_complete,
            description,
            last_modified,
            category_color,
            payload_json,
            first_seen_at,
            last_seen_at,
            last_changed_at
        FROM calendar_item_states
        {where_clause}
        ORDER BY item_type, COALESCE(due_at, start_at, end_at), title
    """

    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        rows = connection.execute(query, params).fetchall()

    records: list[CalendarStateRecord] = []
    for row in rows:
        records.append(
            CalendarStateRecord(
                source=row["source"],
                scope_hash=row["scope_hash"],
                item_type=row["item_type"],
                item_key=row["item_key"],
                external_uid=row["external_uid"],
                calendar_name=row["calendar_name"],
                title=row["title"],
                start_at=row["start_at"],
                end_at=row["end_at"],
                due_at=row["due_at"],
                all_day=bool(row["all_day"]),
                status=row["status"],
                completed=bool(row["completed"]),
                completed_at=row["completed_at"],
                priority=row["priority"],
                percent_complete=row["percent_complete"],
                description=row["description"] or "",
                last_modified=row["last_modified"],
                category_color=row["category_color"],
                payload=_deserialize_json_object(row["payload_json"]) or {},
                first_seen_at=float(row["first_seen_at"]),
                last_seen_at=float(row["last_seen_at"]),
                last_changed_at=float(row["last_changed_at"]),
            )
        )

    return records


def cleanup_calendar_state_records(
    retention_seconds: int = DEFAULT_CALENDAR_STATE_RETENTION_SECONDS,
) -> int:
    db_path = _database_path()
    if not db_path.exists():
        return 0

    threshold = time.time() - max(0, retention_seconds)
    with SQLITE_WRITE_LOCK, _connect(db_path) as connection:
        _ensure_schema(connection)
        cursor = connection.execute(
            "DELETE FROM calendar_item_states WHERE last_seen_at <= ?",
            (threshold,),
        )
        return int(cursor.rowcount or 0)


def _iter_event_records(events: Iterable[CalendarEvent]) -> list[dict[str, Any]]:
    return [
        _build_item_record(
            source=event.source,
            item_type="event",
            external_uid=event.item_uid,
            calendar_name=event.calendar_name,
            title=event.title,
            start_at=event.start,
            end_at=event.end,
            due_at=None,
            all_day=event.all_day,
            status="CONFIRMED",
            completed=False,
            completed_at=None,
            priority=None,
            percent_complete=None,
            description=event.description,
            last_modified=event.last_modified,
            category_color=event.category_color,
            payload=event.to_dict(),
            identity_parts=(event.item_uid, event.start, event.end, event.calendar_name),
        )
        for event in events
    ]


def _iter_todo_records(todos: Iterable[CalendarTodo]) -> list[dict[str, Any]]:
    return [
        _build_item_record(
            source=todo.source,
            item_type="todo",
            external_uid=todo.item_uid,
            calendar_name=todo.calendar_name,
            title=todo.title,
            start_at=todo.start,
            end_at=None,
            due_at=todo.due,
            all_day=todo.start_all_day or todo.due_all_day,
            status=todo.status,
            completed=todo.completed,
            completed_at=todo.completed_at,
            priority=todo.priority,
            percent_complete=todo.percent_complete,
            description=todo.description,
            last_modified=todo.last_modified,
            category_color=todo.category_color,
            payload=todo.to_dict(),
            identity_parts=(todo.item_uid, todo.due or "", todo.start or "", todo.calendar_name),
        )
        for todo in todos
    ]


def _build_item_record(
    *,
    source: str,
    item_type: str,
    external_uid: Optional[str],
    calendar_name: str,
    title: str,
    start_at: Optional[str],
    end_at: Optional[str],
    due_at: Optional[str],
    all_day: bool,
    status: Optional[str],
    completed: bool,
    completed_at: Optional[str],
    priority: Optional[int],
    percent_complete: Optional[int],
    description: str,
    last_modified: Optional[str],
    category_color: Optional[str],
    payload: dict[str, Any],
    identity_parts: tuple[str, ...],
) -> dict[str, Any]:
    item_key = _hash_value("::".join((item_type, *identity_parts)))
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    state_hash = _hash_value(payload_json)
    return {
        "source": source,
        "item_type": item_type,
        "item_key": item_key,
        "external_uid": external_uid,
        "calendar_name": calendar_name,
        "title": title,
        "start_at": start_at,
        "end_at": end_at,
        "due_at": due_at,
        "all_day": all_day,
        "status": status,
        "completed": completed,
        "completed_at": completed_at,
        "priority": priority,
        "percent_complete": percent_complete,
        "description": description,
        "last_modified": last_modified,
        "category_color": category_color,
        "payload_json": payload_json,
        "state_hash": state_hash,
    }


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


def _hash_value(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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
        CREATE TABLE IF NOT EXISTS calendar_item_states (
            source TEXT NOT NULL,
            scope_hash TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_key TEXT NOT NULL,
            external_uid TEXT,
            calendar_name TEXT NOT NULL,
            title TEXT NOT NULL,
            start_at TEXT,
            end_at TEXT,
            due_at TEXT,
            all_day INTEGER NOT NULL,
            status TEXT,
            completed INTEGER NOT NULL,
            completed_at TEXT,
            priority INTEGER,
            percent_complete INTEGER,
            description TEXT,
            last_modified TEXT,
            category_color TEXT,
            state_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            first_seen_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            last_changed_at REAL NOT NULL,
            PRIMARY KEY(source, item_type, item_key)
        )
        """
    )
    _ensure_column(connection, "calendar_item_states", "category_color", "TEXT")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calendar_item_states_due_start
        ON calendar_item_states (item_type, due_at, start_at, completed)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calendar_item_states_scope_hash
        ON calendar_item_states (scope_hash, item_type, completed)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_calendar_item_states_category_color
        ON calendar_item_states (category_color, item_type, completed)
        """
    )


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(row["name"] == column_name for row in rows):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _database_path() -> Path:
    configured_path = os.getenv("YULE_CACHE_DB_PATH")
    if configured_path and configured_path.strip():
        return Path(configured_path).expanduser()

    repo_root = os.getenv("YULE_REPO_ROOT")
    base_dir = Path(repo_root) if repo_root else Path.cwd()
    return base_dir / ".cache" / "yule" / "cache.sqlite3"
