"""F13 source catalog 회귀."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.digest.source_catalog import (
    ROLE_SOURCE_CATALOG,
    all_allowed_hosts,
    host_to_roles,
    sources_for_role,
)


class SourceCatalogTests(unittest.TestCase):
    def test_seven_roles_have_at_least_one_source(self) -> None:
        for role in (
            "tech-lead",
            "product-designer",
            "frontend-engineer",
            "backend-engineer",
            "qa-engineer",
            "ai-engineer",
            "devops-engineer",
        ):
            sources = sources_for_role(role)
            self.assertGreaterEqual(
                len(sources), 1, f"role '{role}' must have at least one source"
            )

    def test_unknown_role_returns_empty(self) -> None:
        self.assertEqual(sources_for_role("nonexistent-role"), ())

    def test_all_allowed_hosts_nonempty(self) -> None:
        self.assertGreater(len(all_allowed_hosts()), 5)

    def test_host_to_roles_matches_catalog(self) -> None:
        # owasp.org 는 backend-engineer 에 속한다
        matched = host_to_roles("owasp.org")
        self.assertIn("backend-engineer", matched)

    def test_host_to_roles_unknown_returns_empty(self) -> None:
        self.assertEqual(host_to_roles("attacker.example.com"), ())

    def test_each_source_has_required_fields(self) -> None:
        for role, sources in ROLE_SOURCE_CATALOG.items():
            for src in sources:
                self.assertTrue(src.host, f"{role} source missing host")
                self.assertTrue(src.feed_url, f"{role}/{src.host} missing feed_url")
                self.assertIn(src.kind, ("rss", "atom", "github_release", "html_list"))
                self.assertGreaterEqual(src.trust, 0.0)
                self.assertLessEqual(src.trust, 1.0)

    def test_no_duplicate_host_within_role(self) -> None:
        for role, sources in ROLE_SOURCE_CATALOG.items():
            hosts = [s.host for s in sources]
            self.assertEqual(len(hosts), len(set(hosts)), f"{role} has duplicate host")


if __name__ == "__main__":
    unittest.main()
