"""Feed parser: deterministic Atom / RSS / GitHub releases atom decode."""

from __future__ import annotations

import unittest
from typing import Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.feed_parser import (
    BytesFetcher,
    FeedParserError,
    canonicalize_feed_title,
    make_feed_live_factory,
    parse_atom_bytes,
    parse_feed_bytes,
    parse_rss_bytes,
    register_safe_feed_providers,
)
from yule_engineering.agents.engineering_intelligence.models import (
    SourceKind,
)
from yule_engineering.agents.engineering_intelligence.providers import (
    ProviderTransport,
    provider_spec_for,
)
from yule_engineering.agents.engineering_intelligence.provider_registry import (
    ProviderAvailability,
    default_registry,
)
from yule_engineering.agents.engineering_intelligence.source_registry import (
    find_source,
)


# ---------------------------------------------------------------------------
# Sample payloads — small enough to read inline
# ---------------------------------------------------------------------------


_ATOM_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Engineering Blog</title>
  <updated>2026-05-09T10:00:00Z</updated>
  <entry>
    <title>Latency-aware retries</title>
    <link rel="alternate" href="https://example.com/posts/latency-aware-retries"/>
    <id>tag:example.com,2026:post-1</id>
    <updated>2026-05-09T09:30:00Z</updated>
    <summary>How retry budgets interact with p99 latency.</summary>
  </entry>
  <entry>
    <title>Schema migration tactics</title>
    <link href="https://example.com/posts/schema-migration"/>
    <id>tag:example.com,2026:post-2</id>
    <published>2026-05-08T08:00:00Z</published>
    <content>Three patterns for online schema migration.</content>
  </entry>
</feed>
"""


_RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Releases</title>
    <link>https://example.com/releases</link>
    <description>Release notes</description>
    <item>
      <title>v1.2.0</title>
      <link>https://example.com/releases/v1.2.0</link>
      <description>Adds OAuth refresh handling.</description>
      <pubDate>Mon, 09 May 2026 12:00:00 GMT</pubDate>
      <guid>https://example.com/releases/v1.2.0</guid>
    </item>
    <item>
      <title>v1.1.5</title>
      <link>https://example.com/releases/v1.1.5</link>
      <description>Security patch for CVE-2026-0001.</description>
      <pubDate>Fri, 02 May 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


_GITHUB_RELEASES_ATOM_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>tag:github.com,2008:https://github.com/example/proj/releases</id>
  <title>Release notes from proj</title>
  <updated>2026-05-09T10:00:00Z</updated>
  <entry>
    <id>tag:github.com,2008:Repository/123/v2.0.0</id>
    <updated>2026-05-09T09:00:00Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/example/proj/releases/tag/v2.0.0"/>
    <title>v2.0.0</title>
    <content type="html">&lt;p&gt;Major release.&lt;/p&gt;</content>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# Atom parser
# ---------------------------------------------------------------------------


class AtomParserTests(unittest.TestCase):
    def setUp(self) -> None:
        spring = find_source("backend-engineer", "spring-blog")
        assert spring is not None
        self.source = spring

    def test_parses_two_entries_with_titles_and_links(self) -> None:
        items = parse_atom_bytes(_ATOM_FIXTURE, source=self.source)
        self.assertEqual(len(items), 2)
        titles = [i.title for i in items]
        self.assertIn("Latency-aware retries", titles)
        self.assertIn("Schema migration tactics", titles)
        urls = {i.source_url for i in items}
        self.assertIn(
            "https://example.com/posts/latency-aware-retries", urls
        )
        self.assertIn("https://example.com/posts/schema-migration", urls)

    def test_prefers_alternate_link_over_first_link(self) -> None:
        # The Atom fixture has rel="alternate" on entry 1; entry 2 only
        # has a bare <link href>. Both should be picked up.
        items = parse_atom_bytes(_ATOM_FIXTURE, source=self.source)
        latency = next(i for i in items if i.title == "Latency-aware retries")
        self.assertEqual(
            latency.source_url,
            "https://example.com/posts/latency-aware-retries",
        )

    def test_falls_back_to_published_when_updated_missing(self) -> None:
        items = parse_atom_bytes(_ATOM_FIXTURE, source=self.source)
        migration = next(
            i for i in items if i.title == "Schema migration tactics"
        )
        self.assertEqual(migration.published_at, "2026-05-08T08:00:00Z")

    def test_falls_back_to_content_when_summary_missing(self) -> None:
        items = parse_atom_bytes(_ATOM_FIXTURE, source=self.source)
        migration = next(
            i for i in items if i.title == "Schema migration tactics"
        )
        self.assertIn("schema migration", migration.summary.lower())

    def test_inherits_source_metadata(self) -> None:
        items = parse_atom_bytes(_ATOM_FIXTURE, source=self.source)
        for item in items:
            self.assertEqual(item.source_name, self.source.name)
            self.assertEqual(item.source_kind, self.source.source_kind)
            self.assertEqual(item.role, self.source.role_tags[0])
            self.assertEqual(tuple(item.stack_tags), self.source.stack_tags)

    def test_malformed_xml_raises_feed_parser_error(self) -> None:
        with self.assertRaises(FeedParserError):
            parse_atom_bytes(b"<feed><entry></feed>", source=self.source)

    def test_empty_feed_returns_empty_tuple(self) -> None:
        empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
        self.assertEqual(parse_atom_bytes(empty, source=self.source), ())

    def test_title_is_canonicalized_for_operator_surface(self) -> None:
        atom = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>[Release] Spring 6.2 Virtual Thread defaults | Engineering Blog</title>
    <link rel="alternate" href="https://example.com/posts/vt"/>
    <updated>2026-05-09T09:30:00Z</updated>
    <summary>summary</summary>
  </entry>
</feed>
"""
        items = parse_atom_bytes(atom, source=self.source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Spring 6.2 Virtual Thread defaults")


# ---------------------------------------------------------------------------
# RSS parser
# ---------------------------------------------------------------------------


class RssParserTests(unittest.TestCase):
    def setUp(self) -> None:
        nvd = find_source("backend-engineer", "cve-nvd")
        assert nvd is not None
        self.source = nvd

    def test_parses_two_items_with_titles_and_links(self) -> None:
        items = parse_rss_bytes(_RSS_FIXTURE, source=self.source)
        self.assertEqual(len(items), 2)
        titles = [i.title for i in items]
        self.assertIn("v1.2.0", titles)
        self.assertIn("v1.1.5", titles)

    def test_pubdate_normalised_to_iso_z(self) -> None:
        items = parse_rss_bytes(_RSS_FIXTURE, source=self.source)
        v120 = next(i for i in items if i.title == "v1.2.0")
        self.assertEqual(v120.published_at, "2026-05-09T12:00:00Z")

    def test_summary_truncated_to_safe_quotation(self) -> None:
        long_desc = "A" * 800
        rss = (
            b'<?xml version="1.0"?>'
            b"<rss version=\"2.0\"><channel>"
            b"<title>x</title><link>https://x</link>"
            b"<description>x</description>"
            b"<item><title>t</title><link>https://x/1</link>"
            b"<description>" + long_desc.encode() + b"</description>"
            b"</item></channel></rss>"
        )
        items = parse_rss_bytes(rss, source=self.source)
        self.assertEqual(len(items), 1)
        self.assertLessEqual(len(items[0].summary), 500)
        self.assertTrue(items[0].summary.endswith("…"))

    def test_malformed_rss_raises_feed_parser_error(self) -> None:
        with self.assertRaises(FeedParserError):
            parse_rss_bytes(b"<rss><channel><item>", source=self.source)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ParseFeedDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.atom_source = find_source("backend-engineer", "spring-blog")
        self.rss_source = find_source("backend-engineer", "cve-nvd")
        assert self.atom_source is not None
        assert self.rss_source is not None

    def test_atom_transport_uses_atom_parser(self) -> None:
        items = parse_feed_bytes(
            _ATOM_FIXTURE,
            source=self.atom_source,
            transport=ProviderTransport.ATOM,
        )
        self.assertEqual(len(items), 2)

    def test_github_releases_atom_uses_atom_parser(self) -> None:
        items = parse_feed_bytes(
            _GITHUB_RELEASES_ATOM_FIXTURE,
            source=self.atom_source,
            transport=ProviderTransport.GITHUB_RELEASES_ATOM,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "v2.0.0")
        self.assertEqual(
            items[0].source_url,
            "https://github.com/example/proj/releases/tag/v2.0.0",
        )

    def test_rss_transport_with_atom_payload_sniffs_and_dispatches(self) -> None:
        # Edge case: an "rss-mode" source endpoint actually serves Atom.
        # The sniff routes the bytes to the atom parser instead of failing.
        items = parse_feed_bytes(
            _ATOM_FIXTURE,
            source=self.rss_source,
            transport=ProviderTransport.RSS,
        )
        self.assertEqual(len(items), 2)

    def test_rss_transport_with_rss_payload_uses_rss_parser(self) -> None:
        items = parse_feed_bytes(
            _RSS_FIXTURE,
            source=self.rss_source,
            transport=ProviderTransport.RSS,
        )
        self.assertEqual(len(items), 2)

    def test_unsupported_transport_raises(self) -> None:
        with self.assertRaises(FeedParserError):
            parse_feed_bytes(
                b"",
                source=self.rss_source,
                transport=ProviderTransport.MANUAL,
            )


# ---------------------------------------------------------------------------
# Title canonicalization
# ---------------------------------------------------------------------------


class CanonicalTitleTests(unittest.TestCase):
    """Discord 이슈방 / GeekNews surface needs short, prefix-free titles."""

    def test_strips_bracketed_prefix_and_trailing_site_marker(self) -> None:
        raw = "[번역] Kubernetes probe edge cases | GitHub"
        self.assertEqual(
            canonicalize_feed_title(raw),
            "Kubernetes probe edge cases",
        )

    def test_strips_paren_notice_and_numeric_prefix(self) -> None:
        raw = "1. (공지) Node 22 LTS 변경점 - Engineering Blog"
        self.assertEqual(
            canonicalize_feed_title(raw),
            "Node 22 LTS 변경점",
        )

    def test_release_prefix_and_engineering_blog_suffix(self) -> None:
        raw = "[Release] Spring 6.2 Virtual Thread defaults | Engineering Blog"
        self.assertEqual(
            canonicalize_feed_title(raw),
            "Spring 6.2 Virtual Thread defaults",
        )

    def test_collapses_internal_whitespace(self) -> None:
        raw = "  [번역]\tKubernetes  probe  edge  cases  "
        self.assertEqual(
            canonicalize_feed_title(raw),
            "Kubernetes probe edge cases",
        )

    def test_falls_back_when_title_is_empty_after_scrub(self) -> None:
        self.assertEqual(
            canonicalize_feed_title("[공지]", fallback="Node 22 LTS 변경점"),
            "Node 22 LTS 변경점",
        )

    def test_truncates_long_titles_on_phrase_boundary(self) -> None:
        raw = (
            "How to tune retry budgets for cross-region failover without "
            "breaking p99 latency or saturating workers"
        )
        out = canonicalize_feed_title(raw, max_chars=48)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 49)
        # Truncation happens on a word boundary — never mid-word.
        self.assertFalse(out.rstrip("…").endswith(" "))

    def test_short_title_passes_through_unchanged(self) -> None:
        raw = "Spring 6.2 가상 스레드"
        self.assertEqual(canonicalize_feed_title(raw), raw)

    def test_empty_title_with_empty_fallback_stays_empty(self) -> None:
        self.assertEqual(canonicalize_feed_title("", fallback=""), "")


# ---------------------------------------------------------------------------
# Live factory glue
# ---------------------------------------------------------------------------


class LiveFactoryGlueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = find_source("backend-engineer", "spring-blog")
        assert self.source is not None
        self.spec = provider_spec_for(self.source)

    def test_factory_calls_parser_through_bytes_fetcher(self) -> None:
        seen_specs: list = []

        def factory(env: Mapping[str, str]) -> BytesFetcher:
            def fetcher(spec):
                seen_specs.append(spec.transport)
                return _ATOM_FIXTURE
            return fetcher

        live_factory = make_feed_live_factory(factory)
        fetcher = live_factory({})
        items = fetcher(self.spec, source=self.source)
        self.assertEqual(len(items), 2)
        self.assertEqual(seen_specs, [ProviderTransport.ATOM])

    def test_factory_swallows_transport_errors(self) -> None:
        def factory(env: Mapping[str, str]) -> BytesFetcher:
            def fetcher(spec):
                raise ConnectionError("simulated")
            return fetcher

        live_factory = make_feed_live_factory(factory)
        items = live_factory({})(self.spec, source=self.source)
        self.assertEqual(items, ())

    def test_factory_swallows_parser_errors(self) -> None:
        def factory(env):
            return lambda spec: b"<not really xml"

        live_factory = make_feed_live_factory(factory)
        items = live_factory({})(self.spec, source=self.source)
        self.assertEqual(items, ())

    def test_factory_returns_empty_for_empty_payload(self) -> None:
        def factory(env):
            return lambda spec: b""

        items = make_feed_live_factory(factory)({})(
            self.spec, source=self.source
        )
        self.assertEqual(items, ())


# ---------------------------------------------------------------------------
# register_safe_feed_providers
# ---------------------------------------------------------------------------


class RegisterSafeFeedProvidersTests(unittest.TestCase):
    def test_registers_three_default_transports(self) -> None:
        registry = default_registry()

        def factory(env):
            return lambda spec: _ATOM_FIXTURE

        registered = register_safe_feed_providers(
            registry, bytes_fetcher_factory=factory
        )
        self.assertEqual(
            set(registered),
            {
                ProviderTransport.RSS,
                ProviderTransport.ATOM,
                ProviderTransport.GITHUB_RELEASES_ATOM,
            },
        )
        # All three now resolve to AVAILABLE when their flag is set.
        env = {
            "YULE_KNOWLEDGE_RSS_LIVE_ENABLED": "true",
            "YULE_KNOWLEDGE_ATOM_LIVE_ENABLED": "true",
            "YULE_KNOWLEDGE_GITHUB_RELEASES_ATOM_LIVE_ENABLED": "true",
        }
        for transport in registered:
            with self.subTest(transport=transport):
                self.assertEqual(
                    registry.evaluate(transport, env=env),
                    ProviderAvailability.AVAILABLE,
                )
        # And other transports stay unaffected.
        self.assertEqual(
            registry.evaluate(ProviderTransport.SITEMAP, env=env),
            ProviderAvailability.NO_LIVE_IMPL,
        )

    def test_skips_transports_not_in_registry(self) -> None:
        from yule_engineering.agents.engineering_intelligence.provider_registry import (
            KnowledgeProviderRegistry,
        )

        empty = KnowledgeProviderRegistry()

        def factory(env):
            return lambda spec: b""

        # Empty registry — nothing is registered, so the helper returns ().
        # No KeyError leaks out.
        self.assertEqual(
            register_safe_feed_providers(empty, bytes_fetcher_factory=factory),
            (),
        )


if __name__ == "__main__":
    unittest.main()
