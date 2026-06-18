"""Source registry + collectors (WT2) — free-first, live vs planned, no fake live.

Proves: repo-local collects offline (real), network collectors parse via an injected
fetcher (no network in CI), planned sources (YouTube/Instagram/Google) ALWAYS return
empty + status=planned (never fake live), and the registry orders live sources
free-cost-first. Pure stdlib → bare CI.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import sources as S
from forgekit_console.sources import collectors as C
from forgekit_console.sources import contract as K


class RepoLocalTests(unittest.TestCase):
    def test_scans_repo_offline_for_gaps(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "apps").mkdir()
        (tmp / "apps" / "big.py").write_text(
            "# TODO a\n# TODO b\n# FIXME c\n" + "x = 1\n" * 5, encoding="utf-8")
        items = C.RepoLocalCollector(tmp).collect(limit=10)
        self.assertTrue(items)
        self.assertTrue(any("TODO/FIXME" in it.title for it in items))
        self.assertEqual(items[0].source_id, "repo-local")


class NetworkCollectorTests(unittest.TestCase):
    def test_hackernews_parses_via_fake_fetcher(self) -> None:
        fake = lambda url: json.dumps({"hits": [
            {"title": "Show HN: forgekit", "url": "http://x", "points": 42}]})
        items = C.hackernews_collector("forgekit", fetcher=fake).collect()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Show HN: forgekit")
        self.assertEqual(items[0].score, 42.0)

    def test_rss_parses_via_fake_fetcher(self) -> None:
        rss = """<rss><channel><item><title>Post A</title>
                 <link>http://a</link></item></channel></rss>"""
        spec = K.SourceSpec("feed", "Feed", K.TYPE_RSS, cost_class=K.COST_FREE)
        items = C.RssCollector(spec, "http://feed", fetcher=lambda u: rss).collect()
        self.assertEqual([i.title for i in items], ["Post A"])

    def test_unreachable_source_returns_empty_not_crash(self) -> None:
        def boom(url):
            raise OSError("offline")
        self.assertEqual(C.hackernews_collector("x", fetcher=boom).collect(), [])


class PlannedSourceTests(unittest.TestCase):
    def test_planned_sources_never_return_fake_data(self) -> None:
        reg = S.default_registry("/tmp/repo", fetcher=lambda u: "{}")
        planned_ids = {c.spec.id for c in reg.planned()}
        self.assertIn("youtube", planned_ids)
        self.assertIn("instagram", planned_ids)
        self.assertIn("google", planned_ids)
        for c in reg.planned():
            self.assertEqual(c.spec.status, K.STATUS_PLANNED)
            self.assertEqual(c.collect(limit=10), [])  # NEVER fake live data


class RegistryPolicyTests(unittest.TestCase):
    def test_live_partition_excludes_planned(self) -> None:
        reg = S.default_registry("/tmp/repo", fetcher=lambda u: "{}")
        live_ids = {c.spec.id for c in reg.live()}
        self.assertIn("repo-local", live_ids)
        self.assertNotIn("youtube", live_ids)

    def test_free_cost_ordered_first(self) -> None:
        reg = S.default_registry("/tmp/repo", fetcher=lambda u: "{}")
        ranks = [c.spec.cost_rank for c in reg.cost_ordered_live()]
        self.assertEqual(ranks, sorted(ranks))            # non-decreasing cost
        self.assertEqual(reg.cost_ordered_live()[0].spec.cost_class, K.COST_FREE)

    def test_collect_all_only_live(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "apps").mkdir()
        reg = S.default_registry(tmp, fetcher=lambda u: "{}")
        got = reg.collect_all(limit_per=5)
        self.assertIn("repo-local", got)
        self.assertNotIn("youtube", got)  # planned not collected
        d = reg.to_dict()
        self.assertTrue(any(s["id"] == "youtube" for s in d["planned"]))


if __name__ == "__main__":
    unittest.main()
