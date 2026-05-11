"""Tests for :mod:`yule_orchestrator.agents.research.providers.live.rss_atom`.

F5 / issue #92. RssAtomProvider + parse_feed 의 행위 회귀.

Hard rails (governance test 는 별도):
  * env OFF / non-allowlisted / non-robots-compliant 시 fetch skip.
  * raw HTML tag 가 summary 로 그대로 노출되지 않는다.
  * 파싱 실패 (잘못된 XML) 는 caller 로 예외 전파 X.
"""

from __future__ import annotations

from datetime import datetime, timezone

from yule_orchestrator.agents.research.providers.live import (
    KIND_ATOM,
    KIND_RSS,
    LiveSource,
    RssAtomProvider,
)
from yule_orchestrator.agents.research.providers.live.rss_atom import parse_feed


# ---------------------------------------------------------------------------
# Fixtures (소형 in-memory feed)
# ---------------------------------------------------------------------------


_RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Demo Feed</title>
  <item>
    <title>New Release v1.2</title>
    <link>https://example.com/r/v1.2</link>
    <description>&lt;p&gt;Fixes &lt;b&gt;bug&lt;/b&gt;.&lt;/p&gt;</description>
    <pubDate>Wed, 01 May 2026 12:00:00 +0000</pubDate>
    <category>release</category>
    <category>fix</category>
  </item>
  <item>
    <title>Note</title>
    <link>https://example.com/r/note</link>
    <description>Plain text.</description>
  </item>
</channel></rss>
"""

_ATOM_BODY = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Demo Atom</title>
  <entry>
    <title>Alpha</title>
    <link href="https://example.com/a/alpha"/>
    <summary>summary alpha</summary>
    <updated>2026-04-30T01:02:03Z</updated>
    <category term="meta"/>
  </entry>
  <entry>
    <title>Beta</title>
    <link href="https://example.com/a/beta"/>
    <content>content beta</content>
    <published>2026-04-29T00:00:00+00:00</published>
  </entry>
</feed>
"""


def _make_source(host="example.com", kind=KIND_RSS, **kw) -> LiveSource:
    return LiveSource(
        host=host,
        kind=kind,
        allow_listed=kw.get("allow_listed", True),
        robots_compliant=kw.get("robots_compliant", True),
        rate_limit_per_sec=kw.get("rate_limit_per_sec", 1.0),
        url=kw.get("url", "https://example.com/feed.xml"),
    )


# ---------------------------------------------------------------------------
# parse_feed
# ---------------------------------------------------------------------------


def test_parse_feed_rss_extracts_entries_with_metadata() -> None:
    entries = parse_feed(_RSS_BODY, kind=KIND_RSS)
    assert len(entries) == 2
    first = entries[0]
    assert first.title == "New Release v1.2"
    assert first.url == "https://example.com/r/v1.2"
    assert first.tags == ("release", "fix")
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None


def test_parse_feed_atom_uses_summary_or_content_fallback() -> None:
    entries = parse_feed(_ATOM_BODY, kind=KIND_ATOM)
    assert len(entries) == 2
    titles = [e.title for e in entries]
    assert titles == ["Alpha", "Beta"]
    # Beta has no <summary>, falls back to <content>.
    beta = entries[1]
    assert beta.summary == "content beta"
    assert beta.url == "https://example.com/a/beta"


def test_parse_feed_atom_parses_dates_including_z_suffix() -> None:
    entries = parse_feed(_ATOM_BODY, kind=KIND_ATOM)
    alpha = entries[0]
    assert alpha.published_at == datetime(2026, 4, 30, 1, 2, 3, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# RssAtomProvider — env / allow-list / robots gates
# ---------------------------------------------------------------------------


def test_provider_returns_empty_when_env_disabled() -> None:
    src = _make_source()
    provider = RssAtomProvider(
        sources=(src,),
        http_fetch=lambda _u: _RSS_BODY,
        env_enabled=False,
    )
    assert provider.ingest() == ()


def test_provider_skips_when_fetcher_is_none_even_if_enabled() -> None:
    provider = RssAtomProvider(
        sources=(_make_source(),),
        http_fetch=None,
        env_enabled=True,
    )
    assert provider.ingest() == ()


def test_provider_skips_non_allowlisted_source() -> None:
    bad = _make_source(allow_listed=False)
    provider = RssAtomProvider(
        sources=(bad,),
        http_fetch=lambda _u: _RSS_BODY,
        env_enabled=True,
    )
    assert provider.ingest() == ()


def test_provider_skips_robots_violation_source() -> None:
    bad = _make_source(robots_compliant=False)
    provider = RssAtomProvider(
        sources=(bad,),
        http_fetch=lambda _u: _RSS_BODY,
        env_enabled=True,
    )
    assert provider.ingest() == ()


def test_provider_ingests_and_redacts_html_in_summary() -> None:
    src = _make_source()
    provider = RssAtomProvider(
        sources=(src,),
        http_fetch=lambda _u: _RSS_BODY,
        env_enabled=True,
    )
    out = provider.ingest()
    assert len(out) == 2
    # HTML tags 가 raw 로 노출되지 않는다 (strip + PasteGuard).
    first = out[0]
    assert "<p>" not in first.summary
    assert "<b>" not in first.summary
    assert "bug" in first.summary
    assert first.tags == ("release", "fix")


def test_provider_isolates_fetch_exceptions() -> None:
    def boom(_url):
        raise RuntimeError("network down")

    provider = RssAtomProvider(
        sources=(_make_source(),),
        http_fetch=boom,
        env_enabled=True,
    )
    # 예외가 caller 로 전파되지 않고 빈 튜플 반환.
    assert provider.ingest() == ()


def test_provider_isolates_parse_errors() -> None:
    provider = RssAtomProvider(
        sources=(_make_source(),),
        http_fetch=lambda _u: "<not-xml",
        env_enabled=True,
    )
    assert provider.ingest() == ()


def test_provider_truncates_entries_to_max() -> None:
    src = _make_source()
    provider = RssAtomProvider(
        sources=(src,),
        http_fetch=lambda _u: _RSS_BODY,
        env_enabled=True,
        max_entries_per_feed=1,
    )
    out = provider.ingest()
    assert len(out) == 1
    assert out[0].title == "New Release v1.2"
