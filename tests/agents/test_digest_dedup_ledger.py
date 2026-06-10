"""F13 dedup ledger 회귀."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.digest.dedup_ledger import (
    DigestDedupLedger,
    hash_title,
    hash_url,
)


class DedupLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "dedup.sqlite3"

    def test_first_post_not_duplicate(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        self.assertFalse(ledger.is_duplicate(
            url="https://owasp.org/a", title="Test", host="owasp.org", dept="engineering",
        ))

    def test_record_then_duplicate(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        ledger.record_posted(
            url="https://owasp.org/a", title="Test", host="owasp.org", dept="engineering",
        )
        self.assertTrue(ledger.is_duplicate(
            url="https://owasp.org/a", title="Test", host="owasp.org", dept="engineering",
        ))

    def test_url_canonicalisation_strips_tracking(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        ledger.record_posted(
            url="https://owasp.org/a", title="t", host="owasp.org", dept="engineering",
        )
        self.assertTrue(ledger.is_duplicate(
            url="https://owasp.org/a?utm_source=feed&ref=x",
            title="t", host="owasp.org", dept="engineering",
        ))

    def test_url_canonicalisation_strips_fragment(self) -> None:
        self.assertEqual(hash_url("https://X.io/a#frag"), hash_url("https://x.io/a"))

    def test_title_hash_normalises_whitespace(self) -> None:
        self.assertEqual(hash_title("Hello  World"), hash_title("hello world"))

    def test_different_dept_not_duplicate(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        ledger.record_posted(
            url="https://owasp.org/a", title="t", host="owasp.org", dept="engineering",
        )
        self.assertFalse(ledger.is_duplicate(
            url="https://owasp.org/a", title="t", host="owasp.org", dept="design",
        ))

    def test_title_only_match_same_host(self) -> None:
        # 다른 URL 이지만 같은 host + title → 중복
        ledger = DigestDedupLedger(self.db, retention_days=14)
        ledger.record_posted(
            url="https://owasp.org/a", title="Same Title", host="owasp.org", dept="engineering",
        )
        self.assertTrue(ledger.is_duplicate(
            url="https://owasp.org/b",  # 다른 path
            title="Same Title", host="owasp.org", dept="engineering",
        ))

    def test_prune_expired(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        # 30일 전 가짜 row 직접 insert
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=30)).isoformat()
        import sqlite3
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(
                "INSERT INTO dept_digest_dedup (url_hash, title_hash, host, dept, posted_at) VALUES (?, ?, ?, ?, ?)",
                ("old_hash", "old_title", "owasp.org", "engineering", old_ts),
            )
            conn.commit()
        count_before = ledger.count_within_retention()
        pruned = ledger.prune_expired()
        self.assertGreaterEqual(pruned, 1)
        count_after = ledger.count_within_retention()
        self.assertEqual(count_before, count_after)  # retention 안 데이터는 그대로


if __name__ == "__main__":
    unittest.main()
