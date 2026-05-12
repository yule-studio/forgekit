"""F13 crawler 회귀 — fake HTTP."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.digest.crawler import (
    CrawlOutcome,
    fetch_source,
    crawl_role,
)
from yule_orchestrator.agents.digest.dedup_ledger import DigestDedupLedger
from yule_orchestrator.agents.digest.source_catalog import AuthoritativeSource


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>OWASP News</title>
    <item>
      <title>XSS Cheat Sheet 갱신</title>
      <link>https://owasp.org/a/xss-2026</link>
      <description>2026 XSS 방어 패턴</description>
      <pubDate>Mon, 12 May 2026 09:00:00 GMT</pubDate>
      <category>security</category>
    </item>
    <item>
      <title>OAuth 2.1 권고</title>
      <link>https://owasp.org/a/oauth-21</link>
      <description>refresh token 회전 권고</description>
      <pubDate>Sun, 11 May 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


_GH_RELEASE_FIXTURE = """[
  {"tag_name": "v6.6.0", "html_url": "https://github.com/spring-projects/spring-security/releases/tag/v6.6.0",
   "body": "Spring Security 6.6", "published_at": "2026-05-10T00:00:00Z", "draft": false, "prerelease": false},
  {"tag_name": "v6.6.0-RC1", "html_url": "https://example/rc", "body": "", "published_at": "2026-04-10T00:00:00Z", "draft": false, "prerelease": true},
  {"tag_name": "v6.5.0", "html_url": "https://github.com/spring-projects/spring-security/releases/tag/v6.5.0",
   "body": "older", "published_at": "2026-03-10T00:00:00Z", "draft": false, "prerelease": false}
]"""


class _StubPoster:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list = []

    def fetch(self, url, *, timeout=15):
        self.calls.append(url)
        return self.response_text


class FetchSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "dedup.sqlite3"
        self.ledger = DigestDedupLedger(self.db, retention_days=14)

    def test_rss_parse_yields_cards(self) -> None:
        source = AuthoritativeSource(
            host="owasp.org",
            feed_url="https://owasp.org/news/feed.xml",
            kind="rss",
            dept_hint="engineering",
        )
        poster = _StubPoster(_RSS_FIXTURE)
        outcome = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=poster)
        self.assertGreaterEqual(outcome.entries_fetched, 2)
        self.assertEqual(len(outcome.cards), 2)
        self.assertIn("XSS", outcome.cards[0].title)
        self.assertEqual(outcome.cards[0].source_host, "owasp.org")
        self.assertEqual(outcome.cards[0].dept_primary, "engineering")

    def test_github_release_filters_prerelease(self) -> None:
        source = AuthoritativeSource(
            host="spring.io",
            feed_url="spring-projects/spring-security",
            kind="github_release",
            dept_hint="engineering",
        )
        poster = _StubPoster(_GH_RELEASE_FIXTURE)
        outcome = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=poster)
        # prerelease 1건 제외 → 2건만
        self.assertEqual(len(outcome.cards), 2)
        # API URL 호출 확인
        self.assertIn("api.github.com", poster.calls[0])

    def test_dedup_skips_second_fetch(self) -> None:
        source = AuthoritativeSource(
            host="owasp.org",
            feed_url="https://owasp.org/news/feed.xml",
            kind="rss",
            dept_hint="engineering",
        )
        poster = _StubPoster(_RSS_FIXTURE)
        out1 = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=poster)
        # 첫 호출 — 카드 게시 가정 후 record
        for card in out1.cards:
            self.ledger.record_posted(
                url=card.url, title=card.title, host=card.source_host, dept=card.dept_primary,
            )
        # 두 번째 호출 — 모두 dedup
        out2 = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=poster)
        self.assertEqual(len(out2.cards), 0)
        self.assertGreaterEqual(out2.skipped_duplicates, 2)

    def test_not_in_allow_list_blocked(self) -> None:
        source = AuthoritativeSource(
            host="attacker.example.com",  # 카탈로그 외
            feed_url="https://attacker.example.com/feed",
            kind="rss",
            dept_hint="engineering",
        )
        poster = _StubPoster(_RSS_FIXTURE)
        outcome = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=poster)
        self.assertEqual(len(outcome.cards), 0)
        self.assertEqual(outcome.blocker_reason, "not in allow-list")
        self.assertEqual(poster.calls, [])  # fetch 시도 자체 안 됨

    def test_http_error_returns_blocker_not_crash(self) -> None:
        import urllib.error

        class _BadPoster:
            calls = []
            def fetch(self, url, *, timeout=15):
                self.calls.append(url)
                raise urllib.error.URLError("connection refused")

        source = AuthoritativeSource(
            host="owasp.org",
            feed_url="https://owasp.org/news/feed.xml",
            kind="rss",
            dept_hint="engineering",
        )
        outcome = fetch_source(source, role="backend-engineer", ledger=self.ledger, http_poster=_BadPoster())
        self.assertIsNotNone(outcome.blocker_reason)
        self.assertIn("URLError", outcome.blocker_reason)


class CrawlRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "dedup.sqlite3"

    def test_crawl_role_returns_outcomes_per_source(self) -> None:
        ledger = DigestDedupLedger(self.db, retention_days=14)
        poster = _StubPoster(_RSS_FIXTURE)
        outcomes = crawl_role("backend-engineer", ledger=ledger, http_poster=poster)
        # backend-engineer 카탈로그 host 수만큼
        from yule_orchestrator.agents.digest.source_catalog import sources_for_role
        expected = len(sources_for_role("backend-engineer"))
        self.assertEqual(len(outcomes), expected)


if __name__ == "__main__":
    unittest.main()
