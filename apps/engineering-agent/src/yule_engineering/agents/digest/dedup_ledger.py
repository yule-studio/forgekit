"""F13 — 디지스트 dedup ledger. url canonical + title hash 기반 24h 재게시 차단.

사용자 design (2026-05-12):
> "중복 제거는 ``url canonical + title hash`` 기준으로 합니다."

SQLite 영속 — 기존 cache.sqlite3 의 새 테이블 `dept_digest_dedup`.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_DEFAULT_DB_PATH = Path.home() / ".cache" / "yule" / "cache.sqlite3"
_TABLE_NAME = "dept_digest_dedup"


@dataclass(frozen=True)
class DedupEntry:
    url_hash: str
    title_hash: str
    posted_at: str
    dept: str


def _canonical_url(url: str) -> str:
    """URL → canonical form: lowercase, strip query/frag, strip trailing slash."""

    if not url:
        return ""
    u = url.strip().lower()
    # strip fragment
    u = u.split("#", 1)[0]
    # strip tracking params (utm_*, gclid, fbclid)
    if "?" in u:
        base, _, query = u.partition("?")
        kept = [
            q for q in query.split("&")
            if not q.startswith(("utm_", "gclid=", "fbclid=", "ref="))
        ]
        u = base + ("?" + "&".join(kept) if kept else "")
    # strip trailing slash
    if u.endswith("/") and len(u) > len("https://x.y/"):
        u = u[:-1]
    return u


def _title_normalised(title: str) -> str:
    """Title → space-normalised lowercase. 작은 표기 차이 무시."""

    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def hash_url(url: str) -> str:
    return hashlib.sha1(_canonical_url(url).encode("utf-8")).hexdigest()


def hash_title(title: str) -> str:
    return hashlib.sha1(_title_normalised(title).encode("utf-8")).hexdigest()


class DigestDedupLedger:
    """SQLite 영속 ledger. 24h 내 같은 url_hash OR (host + title_hash) 재게시 차단.

    24h 가 아닌 retention_hours 로 폭 변경 가능 (env: `YULE_DIGEST_DEDUP_RETENTION_DAYS`).
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        retention_days: int = 14,
    ) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._retention_days = max(1, retention_days)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                    url_hash TEXT NOT NULL,
                    title_hash TEXT NOT NULL,
                    host TEXT NOT NULL,
                    dept TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    PRIMARY KEY (url_hash, dept)
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_title ON {_TABLE_NAME}(title_hash, dept)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE_NAME}_posted ON {_TABLE_NAME}(posted_at)"
            )
            conn.commit()

    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def _retention_cutoff(self) -> str:
        return (self._now() - timedelta(days=self._retention_days)).isoformat()

    def is_duplicate(
        self,
        *,
        url: str,
        title: str,
        host: str,
        dept: str,
    ) -> bool:
        """url_hash OR (host + title_hash) 가 retention 기간 안에 있으면 중복."""

        cutoff = self._retention_cutoff()
        u_hash = hash_url(url)
        t_hash = hash_title(title)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT 1 FROM {_TABLE_NAME}
                WHERE dept = ?
                  AND posted_at > ?
                  AND (url_hash = ? OR (host = ? AND title_hash = ?))
                LIMIT 1
                """,
                (dept, cutoff, u_hash, host, t_hash),
            )
            return cur.fetchone() is not None

    def record_posted(
        self,
        *,
        url: str,
        title: str,
        host: str,
        dept: str,
        posted_at: Optional[datetime] = None,
    ) -> None:
        """게시 직후 ledger 에 기록. 같은 (url_hash, dept) 는 INSERT OR REPLACE."""

        ts = (posted_at or self._now()).isoformat()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {_TABLE_NAME}
                  (url_hash, title_hash, host, dept, posted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (hash_url(url), hash_title(title), host, dept, ts),
            )
            conn.commit()

    def prune_expired(self) -> int:
        """retention 만료 row 삭제. 반환: 삭제된 row count."""

        cutoff = self._retention_cutoff()
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {_TABLE_NAME} WHERE posted_at < ?",
                (cutoff,),
            )
            conn.commit()
            return cur.rowcount or 0

    def count_within_retention(self, *, dept: Optional[str] = None) -> int:
        cutoff = self._retention_cutoff()
        with self._connect() as conn:
            if dept:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE dept = ? AND posted_at > ?",
                    (dept, cutoff),
                )
            else:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE posted_at > ?",
                    (cutoff,),
                )
            row = cur.fetchone()
            return int(row[0]) if row else 0


__all__ = (
    "DigestDedupLedger",
    "DedupEntry",
    "hash_title",
    "hash_url",
)
