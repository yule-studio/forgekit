"""F13 governance 회귀 — hard rails 가 회귀로 깨지지 않게."""

from __future__ import annotations

import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.digest.crawler import fetch_source
from yule_engineering.agents.digest.dedup_ledger import DigestDedupLedger
from yule_engineering.agents.digest.scheduler import SchedulerConfig
from yule_engineering.agents.digest.source_catalog import (
    ROLE_SOURCE_CATALOG,
    AuthoritativeSource,
    all_allowed_hosts,
)


class AllowListGovernanceTests(unittest.TestCase):
    """카탈로그 외 host 절대 fetch 금지 — governance 핵심."""

    def test_catalog_external_host_blocked(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DigestDedupLedger(Path(tmp) / "x.db", retention_days=14)
            bad = AuthoritativeSource(
                host="evil.example.com",  # 카탈로그 외
                feed_url="https://evil.example.com/feed",
                kind="rss",
                dept_hint="engineering",
            )

            class _NeverCalled:
                def fetch(self, url, *, timeout=15):
                    raise AssertionError("must not fetch external host")

            outcome = fetch_source(bad, role="backend-engineer", ledger=ledger, http_poster=_NeverCalled())
            self.assertEqual(outcome.entries_fetched, 0)
            self.assertEqual(outcome.blocker_reason, "not in allow-list")


class SchedulerEnvGovernanceTests(unittest.TestCase):
    def test_default_disabled(self) -> None:
        cfg = SchedulerConfig.from_env({})
        self.assertFalse(cfg.enabled)

    def test_interval_clamped_to_min_1h(self) -> None:
        cfg = SchedulerConfig.from_env({"YULE_DIGEST_SCHEDULER_INTERVAL_HOURS": "0"})
        self.assertGreaterEqual(cfg.interval_hours, 1)

    def test_retention_clamped_to_min_1d(self) -> None:
        cfg = SchedulerConfig.from_env({"YULE_DIGEST_DEDUP_RETENTION_DAYS": "-5"})
        self.assertGreaterEqual(cfg.retention_days, 1)


class CatalogShapeGovernanceTests(unittest.TestCase):
    """사용자 명시 카탈로그 (2026-05-12) 핵심 host 누락 방지."""

    REQUIRED_HOSTS_BY_ROLE = {
        "backend-engineer": ("owasp.org", "postgresql.org"),
        "frontend-engineer": ("developer.mozilla.org", "web.dev"),
        "qa-engineer": ("playwright.dev",),
        "ai-engineer": ("openai.com", "huggingface.co"),
        "devops-engineer": ("kubernetes.io", "docs.docker.com"),
        "product-designer": ("developer.apple.com",),
        "tech-lead": ("infoq.com",),
    }

    def test_required_hosts_present(self) -> None:
        for role, required in self.REQUIRED_HOSTS_BY_ROLE.items():
            hosts = {s.host for s in ROLE_SOURCE_CATALOG.get(role, ())}
            for host in required:
                self.assertIn(
                    host, hosts,
                    f"role '{role}' missing required source host '{host}'",
                )

    def test_all_hosts_unique_no_xss(self) -> None:
        # 카탈로그가 신뢰할 수 있는 host 만 (script/quote 같은 nasty 문자 없어야)
        for host in all_allowed_hosts():
            self.assertTrue(host)
            self.assertNotIn("<", host)
            self.assertNotIn(">", host)
            self.assertNotIn(" ", host)


if __name__ == "__main__":
    unittest.main()
