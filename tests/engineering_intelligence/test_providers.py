"""Live provider spec dispatch — every CollectionMode resolves cleanly."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.models import (
    CollectionMode,
    SourceEntry,
    SourceKind,
    SourceTier,
)
from yule_engineering.agents.engineering_intelligence.providers import (
    LiveProviderSpec,
    ProviderTransport,
    StubLiveSourceFetcher,
    provider_spec_for,
    specs_for_role,
)
from yule_engineering.agents.engineering_intelligence.source_registry import (
    SUPPORTED_ROLES,
    find_source,
)


def _entry(
    *,
    source_id: str,
    base_url: str,
    collection_mode: CollectionMode,
    source_kind: SourceKind = SourceKind.DOCS,
    role_tags: tuple = ("backend-engineer",),
) -> SourceEntry:
    return SourceEntry(
        source_id=source_id,
        name=source_id,
        base_url=base_url,
        role_tags=role_tags,
        stack_tags=("test",),
        source_kind=source_kind,
        collection_mode=collection_mode,
        tier=SourceTier.TIER_2,
    )


class DispatchTests(unittest.TestCase):
    def test_manual_collection_mode_yields_manual_transport(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="m",
                base_url="https://example.com",
                collection_mode=CollectionMode.MANUAL,
            )
        )
        self.assertEqual(spec.transport, ProviderTransport.MANUAL)
        self.assertEqual(spec.parser, "manual")
        self.assertEqual(spec.rate_limit_per_minute, 0)

    def test_github_api_collection_mode_yields_repo_activity(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="g",
                base_url="https://github.com/foo/bar",
                collection_mode=CollectionMode.GITHUB_API,
                source_kind=SourceKind.REPO,
            )
        )
        self.assertEqual(
            spec.transport, ProviderTransport.GITHUB_API_REPO_ACTIVITY
        )
        self.assertIn("YULE_GITHUB_APP_ID", spec.requires_auth_env)
        self.assertEqual(
            spec.request_headers_seed["Accept"],
            "application/vnd.github+json",
        )

    def test_rss_with_github_releases_url_uses_github_releases_transport(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="g-r",
                base_url="https://github.com/foo/bar/releases.atom",
                collection_mode=CollectionMode.RSS,
                source_kind=SourceKind.RELEASE_NOTES,
            )
        )
        self.assertEqual(
            spec.transport, ProviderTransport.GITHUB_RELEASES_ATOM
        )
        self.assertEqual(spec.parser, "atom")

    def test_rss_with_atom_url_uses_atom_transport(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="atom",
                base_url="https://spring.io/blog.atom",
                collection_mode=CollectionMode.RSS,
                source_kind=SourceKind.ENGINEERING_BLOG,
            )
        )
        self.assertEqual(spec.transport, ProviderTransport.ATOM)

    def test_rss_with_xml_url_uses_rss_transport(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="rss",
                base_url="https://web.dev/feed.xml",
                collection_mode=CollectionMode.RSS,
                source_kind=SourceKind.DOCS,
            )
        )
        self.assertEqual(spec.transport, ProviderTransport.RSS)

    def test_sitemap_yields_sitemap_transport(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="map",
                base_url="https://docs.spring.io/spring-framework/reference/index.html",
                collection_mode=CollectionMode.SITEMAP,
                source_kind=SourceKind.DOCS,
            )
        )
        self.assertEqual(spec.transport, ProviderTransport.SITEMAP)
        self.assertEqual(spec.parser, "sitemap")

    def test_html_list_yields_html_list(self) -> None:
        spec = provider_spec_for(
            _entry(
                source_id="html",
                base_url="https://www.postgresql.org/docs/release/",
                collection_mode=CollectionMode.HTML_LIST,
                source_kind=SourceKind.RELEASE_NOTES,
            )
        )
        self.assertEqual(spec.transport, ProviderTransport.HTML_LIST)


class RegistryDispatchTests(unittest.TestCase):
    def test_specs_for_every_role_resolve_without_raising(self) -> None:
        for role in SUPPORTED_ROLES:
            specs = specs_for_role(role)
            self.assertGreater(len(specs), 0)
            for spec in specs:
                self.assertIsInstance(spec, LiveProviderSpec)
                # endpoint should be a non-empty URL even for MANUAL.
                self.assertTrue(spec.endpoint)
                # parser is one of the known strings.
                self.assertIn(
                    spec.parser,
                    {
                        "rss",
                        "atom",
                        "sitemap",
                        "html_list",
                        "html_detail",
                        "github_api",
                        "manual",
                    },
                )

    def test_owasp_review_required_still_gets_a_manual_spec(self) -> None:
        # Provider dispatch ignores review_required — that's a planner
        # concern. The spec still resolves so the operator can click
        # through to the URL on the dashboard.
        owasp = find_source("backend-engineer", "owasp-top-10")
        assert owasp is not None
        spec = provider_spec_for(owasp)
        self.assertEqual(spec.transport, ProviderTransport.MANUAL)


class StubFetcherTests(unittest.TestCase):
    def test_stub_records_specs_returns_empty(self) -> None:
        owasp = find_source("backend-engineer", "owasp-top-10")
        assert owasp is not None
        spec = provider_spec_for(owasp)
        fetcher = StubLiveSourceFetcher()
        items = fetcher(spec, source=owasp)
        self.assertEqual(items, ())
        self.assertEqual(fetcher.seen, [spec])


if __name__ == "__main__":
    unittest.main()
