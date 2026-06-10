"""Persistent mistake ledger — F2 / issue #89.

Cross-session counterpart to the round-1 session-extra ledger under
:mod:`yule_engineering.agents.lifecycle.mistake_ledger`. Where round 1
lives on ``session.extra`` so the existing SQLite session row carries
it forward, F2 needs the ledger to *survive process restarts* and to
be queryable without first hydrating a session — the preflight hook
fires at the very top of ``coding_executor_worker._run_pipeline``
before any session is touched.

Storage shape:

  * A single SQLite table, ``mistake_ledger``. Created with
    ``CREATE TABLE IF NOT EXISTS`` so it co-exists with whatever else
    the cache DB already owns (``task_completion_events``,
    ``json_cache``).
  * Default location matches :mod:`yule_engineering.storage` — the
    operator's ``$YULE_CACHE_DB_PATH`` env wins, then a per-repo
    ``.cache/yule/cache.sqlite3`` fallback.
  * A caller that already owns a SQLite path (tests, or an embedded
    runtime) may pass ``database_path=`` to scope the ledger to its
    own file.

The dataclass shape mirrors the issue #89 Acceptance Criteria 1:

  ``id / role / pattern / signature / first_seen / last_seen /
   occurrences / blocker_level / postmortem_ref / resolved_at``

The optional ``resolved_at`` field lets us implement Acceptance
Criteria 3's hard rail — auto-dismiss is forbidden, only an explicit
operator call to :meth:`MistakeLedger.resolve` flips a record.
``prune_old_resolved`` then deletes records older than a retention
window so the table never grows unbounded.

Similarity matching for :meth:`MistakeLedger.find_similar` uses token
Jaccard similarity on the signature so the preflight hook can match a
*near-duplicate* signature (e.g. ``"ci:test failed on auth/login"``
vs. ``"ci:test failed on auth/register"``) without exact-string
equality. The threshold is configurable per call.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from yule_storage._sqlite import SQLITE_WRITE_LOCK


DEFAULT_SQLITE_BUSY_TIMEOUT_MS: int = 30_000
DEFAULT_PRUNE_RETENTION_DAYS: int = 180


# ---------------------------------------------------------------------------
# Blocker level
# ---------------------------------------------------------------------------


class BlockerLevel(str, Enum):
    """Severity of a recorded mistake.

    Ordering (low → high): ``ADVISORY < WARNING < BLOCK``. The
    preflight hook uses :func:`max_blocker_level` to combine multiple
    matched records into one verdict.
    """

    ADVISORY = "ADVISORY"
    WARNING = "WARNING"
    BLOCK = "BLOCK"


_LEVEL_ORDER: Mapping[BlockerLevel, int] = {
    BlockerLevel.ADVISORY: 0,
    BlockerLevel.WARNING: 1,
    BlockerLevel.BLOCK: 2,
}


def max_blocker_level(*levels: BlockerLevel) -> BlockerLevel:
    """Return the highest level among *levels* (ADVISORY by default)."""

    if not levels:
        return BlockerLevel.ADVISORY
    return max(levels, key=_LEVEL_ORDER.__getitem__)


def _coerce_blocker_level(value: Any) -> BlockerLevel:
    """Best-effort coerce a payload value into :class:`BlockerLevel`."""

    if isinstance(value, BlockerLevel):
        return value
    text = str(value or "").strip().upper()
    if text in BlockerLevel.__members__:
        return BlockerLevel[text]
    return BlockerLevel.ADVISORY


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MistakeRecord:
    """One row in the durable ledger.

    Frozen so callers can ferry the record between threads / worker
    boundaries without worrying about accidental mutation. Updates
    happen through :meth:`MistakeLedger.record_mistake` (which
    increments ``occurrences`` and advances ``last_seen``) or through
    explicit operator calls (:meth:`MistakeLedger.resolve`).
    """

    id: str
    role: str
    pattern: str
    signature: str
    first_seen: str
    last_seen: str
    occurrences: int
    blocker_level: BlockerLevel
    postmortem_ref: Optional[str] = None
    resolved_at: Optional[str] = None

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "pattern": self.pattern,
            "signature": self.signature,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "occurrences": self.occurrences,
            "blocker_level": self.blocker_level.value,
            "postmortem_ref": self.postmortem_ref,
            "resolved_at": self.resolved_at,
        }

    def is_resolved(self) -> bool:
        return bool(self.resolved_at)


# ---------------------------------------------------------------------------
# Similarity (token Jaccard)
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(value: str) -> frozenset[str]:
    text = str(value or "").strip().lower()
    if not text:
        return frozenset()
    return frozenset(_TOKEN_RE.findall(text))


def jaccard_similarity(a: str, b: str) -> float:
    """Token Jaccard similarity between two strings.

    Used by :meth:`MistakeLedger.find_similar`. Lower-cased and ASCII
    word-tokenised so callers can pass freeform signatures without
    pre-normalising. Empty inputs return 0.0.
    """

    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


class _LedgerConnection:
    """Lightweight wrapper that makes both fresh and persistent SQLite
    connections usable with ``with`` syntax while only closing the
    fresh ones on exit.

    For an owned connection (on-disk path): commits on success,
    rolls back on exception, then closes. For a borrowed
    persistent connection (``:memory:`` path): commits / rolls back
    but never closes — the caller owns the lifetime.
    """

    def __init__(self, connection: sqlite3.Connection, *, owns: bool) -> None:
        self._connection = connection
        self._owns = owns

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            if self._owns:
                self._connection.close()


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class MistakeLedger:
    """SQLite-backed durable ledger for repeated role mistakes.

    The class is intentionally thin: callers build / share the ledger
    by file path. Tests construct an in-memory instance via the
    ``database_path=":memory:"`` shortcut or a tmp file; production
    code reuses the operator cache database so an audit reader can
    correlate mistakes with task-completion stats from the same file.

    Thread-safety: SQLite write operations are serialised through the
    shared :data:`SQLITE_WRITE_LOCK` so this ledger can co-exist with
    other producers writing to the same DB file (the round-1 task
    history module follows the same convention).
    """

    TABLE_NAME: str = "mistake_ledger"

    def __init__(
        self,
        *,
        database_path: Optional[str | Path] = None,
        busy_timeout_ms: Optional[int] = None,
    ) -> None:
        self._database_path = _resolve_database_path(database_path)
        self._busy_timeout_ms = (
            int(busy_timeout_ms)
            if busy_timeout_ms and int(busy_timeout_ms) > 0
            else _resolve_busy_timeout_ms()
        )
        self._ensure_parent()
        # ``:memory:`` databases cannot share state across connections,
        # so we hold a single long-lived connection for the lifetime of
        # the ledger. On-disk databases keep the original per-call
        # connection model so they co-exist with other producers.
        self._persistent_connection: Optional[sqlite3.Connection] = None
        if self._database_path == ":memory:":
            self._persistent_connection = self._open_connection()
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            connection.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_mistake(
        self,
        *,
        role: str,
        pattern: str,
        signature: str,
        postmortem_ref: Optional[str] = None,
        blocker_level: BlockerLevel = BlockerLevel.ADVISORY,
        when: Optional[str] = None,
    ) -> MistakeRecord:
        """Insert (or bump) a record for *(role, pattern, signature)*.

        Resolution rule:

          * ``(role, pattern, signature)`` already in the ledger →
            ``occurrences += 1``, ``last_seen`` advances, and the
            blocker level can only escalate. ``postmortem_ref`` is
            updated when supplied and the existing row had none.
            A resolved row is **re-opened** (``resolved_at`` cleared)
            so the same mistake biting again unconditionally raises
            the preflight signal.
          * New tuple → fresh row with ``occurrences=1`` and the
            supplied blocker level.

        The original record (if any) is never mutated in place — the
        method returns the persisted record.
        """

        role_value = _require_non_empty("role", role)
        pattern_value = _require_non_empty("pattern", pattern)
        signature_value = _require_non_empty("signature", signature)
        level = _coerce_blocker_level(blocker_level)
        when_iso = (when or _utc_now_iso()).strip() or _utc_now_iso()
        ref_value = _optional_str(postmortem_ref)

        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            existing_row = connection.execute(
                f"""
                SELECT * FROM {self.TABLE_NAME}
                WHERE role = ? AND pattern = ? AND signature = ?
                LIMIT 1
                """,
                (role_value, pattern_value, signature_value),
            ).fetchone()
            if existing_row is None:
                record = MistakeRecord(
                    id=_new_record_id(),
                    role=role_value,
                    pattern=pattern_value,
                    signature=signature_value,
                    first_seen=when_iso,
                    last_seen=when_iso,
                    occurrences=1,
                    blocker_level=level,
                    postmortem_ref=ref_value,
                    resolved_at=None,
                )
                connection.execute(
                    f"""
                    INSERT INTO {self.TABLE_NAME} (
                        id, role, pattern, signature,
                        first_seen, last_seen, occurrences,
                        blocker_level, postmortem_ref, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.role,
                        record.pattern,
                        record.signature,
                        record.first_seen,
                        record.last_seen,
                        record.occurrences,
                        record.blocker_level.value,
                        record.postmortem_ref,
                        record.resolved_at,
                    ),
                )
                return record

            current = _row_to_record(existing_row)
            new_level = max_blocker_level(current.blocker_level, level)
            updated_ref = current.postmortem_ref or ref_value
            updated = MistakeRecord(
                id=current.id,
                role=current.role,
                pattern=current.pattern,
                signature=current.signature,
                first_seen=current.first_seen,
                last_seen=when_iso,
                occurrences=current.occurrences + 1,
                blocker_level=new_level,
                postmortem_ref=updated_ref,
                resolved_at=None,
            )
            connection.execute(
                f"""
                UPDATE {self.TABLE_NAME}
                SET last_seen = ?,
                    occurrences = ?,
                    blocker_level = ?,
                    postmortem_ref = ?,
                    resolved_at = NULL
                WHERE id = ?
                """,
                (
                    updated.last_seen,
                    updated.occurrences,
                    updated.blocker_level.value,
                    updated.postmortem_ref,
                    updated.id,
                ),
            )
            return updated

    def find_similar(
        self,
        *,
        role: str,
        signature: str,
        threshold: float = 0.7,
        include_resolved: bool = False,
    ) -> Tuple[MistakeRecord, ...]:
        """Return records whose signature is similar to *signature*.

        Similarity is the token Jaccard score; values >= *threshold*
        are kept. The result is sorted by (similarity desc,
        occurrences desc, last_seen desc) so the strongest evidence
        ends up first — that is what the preflight hook reports as the
        leading reason.

        ``include_resolved`` defaults to False because the preflight
        hook only cares about *active* mistakes; resolved rows are
        kept for the postmortem trail but should not block the next
        run.
        """

        role_value = str(role or "").strip()
        if not role_value:
            return ()
        sig_value = str(signature or "").strip()
        if not sig_value:
            return ()
        try:
            cutoff = max(0.0, float(threshold))
        except (TypeError, ValueError):
            cutoff = 0.7

        rows = self._fetch_role_rows(role_value, include_resolved=include_resolved)
        scored: list[tuple[float, MistakeRecord]] = []
        for row in rows:
            record = _row_to_record(row)
            score = jaccard_similarity(record.signature, sig_value)
            if score >= cutoff:
                scored.append((score, record))
        scored.sort(
            key=lambda pair: (
                -pair[0],
                -pair[1].occurrences,
                pair[1].last_seen,
            ),
            reverse=False,
        )
        # We want highest score first; secondary keys also descending
        # — sort with negatives, then strip the score.
        return tuple(record for _, record in scored)

    def list_for_role(
        self,
        role: str,
        *,
        limit: int = 20,
        include_resolved: bool = False,
    ) -> Tuple[MistakeRecord, ...]:
        """Return the most-recent *limit* records for *role*.

        Ordered by ``last_seen`` descending; resolved rows are
        excluded unless *include_resolved* is True.
        """

        role_value = str(role or "").strip()
        if not role_value:
            return ()
        try:
            limit_value = max(1, int(limit))
        except (TypeError, ValueError):
            limit_value = 20

        clauses = ["role = ?"]
        params: list[Any] = [role_value]
        if not include_resolved:
            clauses.append("resolved_at IS NULL")
        where = " AND ".join(clauses)

        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                f"""
                SELECT * FROM {self.TABLE_NAME}
                WHERE {where}
                ORDER BY last_seen DESC, id ASC
                LIMIT ?
                """,
                (*params, limit_value),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def get(self, record_id: str) -> Optional[MistakeRecord]:
        """Fetch a single record by id (``None`` if missing)."""

        rid = str(record_id or "").strip()
        if not rid:
            return None
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                f"SELECT * FROM {self.TABLE_NAME} WHERE id = ? LIMIT 1",
                (rid,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def resolve(
        self,
        record_id: str,
        *,
        resolved_at: Optional[str] = None,
    ) -> Optional[MistakeRecord]:
        """Mark *record_id* resolved.

        Returns the updated record, or ``None`` when no such row
        exists. Calling :meth:`resolve` on an already-resolved record
        is a no-op (the existing ``resolved_at`` stamp is preserved).

        Auto-dismiss is intentionally not exposed — only this explicit
        method flips the flag. That preserves Acceptance Criteria 3's
        "운영자 명시 dismiss/resolve API" rail.
        """

        rid = str(record_id or "").strip()
        if not rid:
            return None
        when_iso = (resolved_at or _utc_now_iso()).strip() or _utc_now_iso()
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            row = connection.execute(
                f"SELECT * FROM {self.TABLE_NAME} WHERE id = ? LIMIT 1",
                (rid,),
            ).fetchone()
            if row is None:
                return None
            current = _row_to_record(row)
            if current.is_resolved():
                return current
            connection.execute(
                f"UPDATE {self.TABLE_NAME} SET resolved_at = ? WHERE id = ?",
                (when_iso, rid),
            )
            return replace(current, resolved_at=when_iso)

    def prune_old_resolved(
        self,
        retention_days: int = DEFAULT_PRUNE_RETENTION_DAYS,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        """Delete resolved records older than *retention_days*.

        Returns the number of rows deleted. Active (unresolved)
        records are never touched — only the explicit :meth:`resolve`
        path can mark a row prune-eligible.
        """

        try:
            days_value = max(0, int(retention_days))
        except (TypeError, ValueError):
            days_value = DEFAULT_PRUNE_RETENTION_DAYS
        reference = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        cutoff = reference - timedelta(days=days_value)
        cutoff_iso = cutoff.replace(microsecond=0).isoformat()

        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            cursor = connection.execute(
                f"""
                DELETE FROM {self.TABLE_NAME}
                WHERE resolved_at IS NOT NULL
                  AND resolved_at <= ?
                """,
                (cutoff_iso,),
            )
            return int(cursor.rowcount or 0)

    def all_records(
        self,
        *,
        include_resolved: bool = True,
    ) -> Tuple[MistakeRecord, ...]:
        """Diagnostic helper: full ledger snapshot.

        Ordered by ``last_seen`` descending. Tests use this to verify
        SQLite round-trips without relying on
        :meth:`list_for_role`'s per-role filter.
        """

        clauses: list[str] = []
        if not include_resolved:
            clauses.append("resolved_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                f"SELECT * FROM {self.TABLE_NAME}{where} ORDER BY last_seen DESC, id ASC"
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    @property
    def database_path(self) -> str:
        return self._database_path

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_role_rows(
        self,
        role: str,
        *,
        include_resolved: bool,
    ) -> Sequence[sqlite3.Row]:
        clauses = ["role = ?"]
        params: list[Any] = [role]
        if not include_resolved:
            clauses.append("resolved_at IS NULL")
        where = " AND ".join(clauses)
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            self._ensure_schema(connection)
            return connection.execute(
                f"""
                SELECT * FROM {self.TABLE_NAME}
                WHERE {where}
                """,
                tuple(params),
            ).fetchall()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                pattern TEXT NOT NULL,
                signature TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                occurrences INTEGER NOT NULL,
                blocker_level TEXT NOT NULL,
                postmortem_ref TEXT,
                resolved_at TEXT
            )
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.TABLE_NAME}_role_seen
            ON {self.TABLE_NAME} (role, last_seen)
            """
        )
        connection.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{self.TABLE_NAME}_role_pattern_signature
            ON {self.TABLE_NAME} (role, pattern, signature)
            """
        )

    def _ensure_parent(self) -> None:
        path = self._database_path
        if path == ":memory:":
            return
        parent = Path(path).expanduser().parent
        if str(parent) and parent != Path(""):
            parent.mkdir(parents=True, exist_ok=True)

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        except sqlite3.OperationalError:
            pass
        if self._database_path != ":memory:":
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute("PRAGMA synchronous = NORMAL")
            except sqlite3.OperationalError:
                pass
        return connection

    def _connect(self) -> "_LedgerConnection":
        """Acquire a connection wrapper.

        For ``:memory:`` databases we re-use the single
        :attr:`_persistent_connection` so callers see the same data
        across method calls. For on-disk databases we open a fresh
        connection so the ledger plays nicely alongside other
        producers writing to the same file.
        """

        if self._persistent_connection is not None:
            return _LedgerConnection(
                self._persistent_connection,
                owns=False,
            )
        return _LedgerConnection(self._open_connection(), owns=True)

    def close(self) -> None:
        """Close the persistent connection (no-op for on-disk paths).

        Tests that build a fresh :class:`MistakeLedger` per test case
        should call ``close()`` in ``tearDown`` so the underlying
        ``:memory:`` connection is released.
        """

        if self._persistent_connection is not None:
            try:
                self._persistent_connection.close()
            finally:
                self._persistent_connection = None


# ---------------------------------------------------------------------------
# Postmortem → mistake candidate
# ---------------------------------------------------------------------------


def mistake_candidate_from_postmortem(
    audit_entry: Optional[Mapping[str, Any]],
) -> Optional[MistakeRecord]:
    """Project a postmortem audit entry into a *candidate* record.

    The helper is **deterministic** — the same audit entry always
    yields a record with the same ``role / pattern / signature /
    blocker_level / postmortem_ref``. The ``id``, ``first_seen``,
    ``last_seen`` fields are derived from the audit entry too
    (``decision_id`` / ``entry_id`` for id; ``recorded_at`` for the
    timestamps), so calling this twice on the same entry returns
    equal records — never two random ids.

    Returns ``None`` when *audit_entry* doesn't look like a postmortem
    (missing role, missing summary/reason, or wrong action verb).

    The caller is responsible for *persisting* — this helper just
    produces the candidate so the post-mortem producer can decide
    whether to feed it to :meth:`MistakeLedger.record_mistake`.
    """

    if not isinstance(audit_entry, Mapping):
        return None
    action = str(audit_entry.get("action") or "").strip().lower()
    if action and action not in {
        "failure_postmortem_create",
        "failure_audit_record",
        "retry_audit_record",
        "blocked_completion",
        "postmortem",
    }:
        return None
    role = _optional_str(
        audit_entry.get("role")
        or audit_entry.get("role_id")
        or audit_entry.get("actor")
    )
    if not role:
        return None
    reason = str(audit_entry.get("reason") or "").strip()
    summary = str(audit_entry.get("summary") or "").strip()
    signature_source = reason or summary
    if not signature_source:
        return None
    pattern = _derive_pattern(action=action, audit_entry=audit_entry)
    blocker_level = _coerce_blocker_level(
        audit_entry.get("blocker_level")
        or _default_blocker_for_pattern(pattern)
    )
    ref = _optional_str(
        audit_entry.get("postmortem_ref")
        or audit_entry.get("entry_id")
        or audit_entry.get("decision_id")
    )
    when = _optional_str(audit_entry.get("recorded_at")) or _utc_now_iso()
    candidate_id = (
        _optional_str(audit_entry.get("entry_id"))
        or _optional_str(audit_entry.get("decision_id"))
        or _deterministic_id(role=role, pattern=pattern, signature=signature_source)
    )
    return MistakeRecord(
        id=candidate_id,
        role=role,
        pattern=pattern,
        signature=signature_source,
        first_seen=when,
        last_seen=when,
        occurrences=1,
        blocker_level=blocker_level,
        postmortem_ref=ref,
        resolved_at=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: sqlite3.Row) -> MistakeRecord:
    return MistakeRecord(
        id=str(row["id"]),
        role=str(row["role"]),
        pattern=str(row["pattern"]),
        signature=str(row["signature"]),
        first_seen=str(row["first_seen"]),
        last_seen=str(row["last_seen"]),
        occurrences=int(row["occurrences"]),
        blocker_level=_coerce_blocker_level(row["blocker_level"]),
        postmortem_ref=_optional_str(row["postmortem_ref"]),
        resolved_at=_optional_str(row["resolved_at"]),
    )


def _derive_pattern(*, action: str, audit_entry: Mapping[str, Any]) -> str:
    raw = (
        audit_entry.get("pattern")
        or audit_entry.get("job_type")
        or audit_entry.get("topic_key")
        or action
        or "postmortem"
    )
    text = str(raw or "").strip().lower()
    text = re.sub(r"\s+", "_", text)[:64]
    return text or "postmortem"


def _default_blocker_for_pattern(pattern: str) -> BlockerLevel:
    lowered = (pattern or "").lower()
    if lowered.startswith("ci"):
        return BlockerLevel.WARNING
    if "secret" in lowered or "protected" in lowered or "force_push" in lowered:
        return BlockerLevel.BLOCK
    return BlockerLevel.ADVISORY


def _deterministic_id(*, role: str, pattern: str, signature: str) -> str:
    digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mistake-ledger:{role}|{pattern}|{signature}",
    ).hex
    return f"pm-{digest[:24]}"


def _new_record_id() -> str:
    return f"ml-{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:12]}"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_non_empty(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required and must be non-empty")
    return text


def _resolve_database_path(database_path: Optional[str | Path]) -> str:
    if database_path is not None:
        if isinstance(database_path, Path):
            return str(database_path.expanduser())
        text = str(database_path).strip()
        if text:
            if text == ":memory:":
                return text
            return str(Path(text).expanduser())
    configured = os.getenv("YULE_CACHE_DB_PATH")
    if configured and configured.strip():
        return str(Path(configured.strip()).expanduser())
    repo_root = os.getenv("YULE_REPO_ROOT")
    base_dir = Path(repo_root) if repo_root else Path.cwd()
    return str(base_dir / ".cache" / "yule" / "cache.sqlite3")


def _resolve_busy_timeout_ms() -> int:
    configured = os.getenv("YULE_SQLITE_BUSY_TIMEOUT_MS")
    if configured and configured.strip():
        try:
            return max(1000, int(configured.strip()))
        except ValueError:
            return DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    return DEFAULT_SQLITE_BUSY_TIMEOUT_MS


__all__ = (
    "DEFAULT_PRUNE_RETENTION_DAYS",
    "BlockerLevel",
    "MistakeLedger",
    "MistakeRecord",
    "jaccard_similarity",
    "max_blocker_level",
    "mistake_candidate_from_postmortem",
)
