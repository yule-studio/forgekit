"""Collector — fake adapter, daily limit 5, dedup, sort order."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.collector import (
    FakeSourceCollectorAdapter,
    collect_for_role,
    utc_now_iso,
)
from yule_engineering.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceKind,
)


def _item(
    *,
    role: str,
    title: str,
    source_url: str,
    source_name: str = "Spring Engineering Blog",
    source_kind: SourceKind = SourceKind.ENGINEERING_BLOG,
    importance: Importance = Importance.MEDIUM,
    topic_key: str = "",
    collected_at: str = "2026-05-08T00:00:00Z",
) -> EngineeringKnowledgeItem:
    return EngineeringKnowledgeItem(
        item_id=f"{role}-{title}",
        topic_key=topic_key or title.lower().replace(" ", "-"),
        title=title,
        role=role,
        stack_tags=("test",),
        source_name=source_name,
        source_url=source_url,
        source_kind=source_kind,
        collected_at=collected_at,
        importance=importance,
    )


class AdapterCallTests(unittest.TestCase):
    def test_visits_every_auto_collectable_source(self) -> None:
        adapter = FakeSourceCollectorAdapter({})
        result = collect_for_role("backend-engineer", adapter=adapter)
        self.assertGreater(len(adapter.calls), 0)
        # Every visited id must be an auto-collectable source for the role.
        from yule_engineering.agents.engineering_intelligence.source_registry import (
            auto_collectable_sources,
        )

        expected_ids = {s.source_id for s in auto_collectable_sources("backend-engineer")}
        self.assertEqual(set(adapter.calls), expected_ids)
        self.assertEqual(set(result.visited_source_ids), expected_ids)

    def test_adapter_failure_is_swallowed(self) -> None:
        class BoomAdapter:
            calls: list = []

            def __call__(self, source):  # type: ignore[no-untyped-def]
                BoomAdapter.calls.append(source.source_id)
                raise RuntimeError("transport down")

        warnings: list = []
        result = collect_for_role(
            "backend-engineer",
            adapter=BoomAdapter(),
            warn=warnings.append,
        )
        # Run completed without raising; warnings recorded one per source.
        self.assertEqual(result.accepted, ())
        self.assertGreaterEqual(len(warnings), 1)
        # Warning must mention the exception type so the operator can grep.
        self.assertTrue(any("RuntimeError" in w for w in warnings))


class DailyLimitTests(unittest.TestCase):
    def test_daily_limit_truncates_to_five(self) -> None:
        # Build 8 items returned from a single source; expect 5 accepted.
        items = [
            _item(
                role="backend-engineer",
                title=f"Spring 6.{n} 출시",
                source_url=f"https://spring.io/blog/spring-6-{n}",
                topic_key=f"spring-6-{n}",
                importance=Importance.HIGH,
            )
            for n in range(8)
        ]
        adapter = FakeSourceCollectorAdapter({"spring-blog": items})
        result = collect_for_role("backend-engineer", adapter=adapter)
        self.assertEqual(len(result.accepted), 5)
        # The remaining 3 are recorded as daily_limit_exceeded.
        overflow_reasons = [r["reason"] for r in result.rejected]
        self.assertIn("daily_limit_exceeded", overflow_reasons)

    def test_explicit_limit_override_applies(self) -> None:
        items = [
            _item(
                role="backend-engineer",
                title=f"Item {n}",
                source_url=f"https://example.com/{n}",
                topic_key=f"item-{n}",
            )
            for n in range(5)
        ]
        adapter = FakeSourceCollectorAdapter({"spring-blog": items})
        result = collect_for_role(
            "backend-engineer",
            adapter=adapter,
            daily_limit=2,
        )
        self.assertEqual(len(result.accepted), 2)


class DedupAndSortTests(unittest.TestCase):
    def test_duplicate_url_dropped(self) -> None:
        a = _item(
            role="frontend-engineer",
            title="React 19 RC",
            source_url="https://react.dev/blog/2026/05/01/react-19",
            source_name="React Blog",
            topic_key="react-19-rc",
        )
        b = _item(
            role="frontend-engineer",
            title="React 19 RC (mirror)",
            source_url="https://react.dev/blog/2026/05/01/react-19",
            source_name="React Blog",
            topic_key="react-19-rc-mirror",
        )
        adapter = FakeSourceCollectorAdapter({"react-blog": [a, b]})
        result = collect_for_role("frontend-engineer", adapter=adapter)
        self.assertEqual(len(result.accepted), 1)
        # The dropped second item shows up in the rejection audit.
        urls = [r.get("url") for r in result.rejected if "url" in r]
        self.assertTrue(urls)

    def test_duplicate_topic_key_dropped(self) -> None:
        a = _item(
            role="ai-engineer",
            title="OpenAI ships X",
            source_url="https://openai.com/news/x",
            source_name="OpenAI News & Research",
            topic_key="openai-x",
        )
        b = _item(
            role="ai-engineer",
            title="Same topic, different title",
            source_url="https://openai.com/news/x-mirror",
            source_name="OpenAI News & Research",
            topic_key="openai-x",
        )
        adapter = FakeSourceCollectorAdapter({"openai-news": [a, b]})
        result = collect_for_role("ai-engineer", adapter=adapter)
        self.assertEqual(len(result.accepted), 1)

    def test_official_tier_1_wins_over_community_when_competing(self) -> None:
        # Both items in same role's bag — the Tier 1 source-named
        # one should land at index 0 of accepted.
        official = _item(
            role="frontend-engineer",
            title="React 19 stable",
            source_url="https://react.dev/blog/2026/06/01/react-19-stable",
            source_name="React Blog",  # Tier 1
            topic_key="react-19-stable",
            importance=Importance.HIGH,
        )
        community = _item(
            role="frontend-engineer",
            title="Some Medium take",
            source_url="https://medium.com/foo/react-take",
            source_name="medium-foo",  # not in registry → Tier 4 default
            topic_key="medium-react-take",
            importance=Importance.HIGH,
        )
        adapter = FakeSourceCollectorAdapter(
            {"react-blog": [community, official]}
        )
        result = collect_for_role("frontend-engineer", adapter=adapter)
        self.assertEqual(result.accepted[0].source_name, "React Blog")

    def test_dedup_key_attached_when_missing(self) -> None:
        item = _item(
            role="qa-engineer",
            title="Playwright 1.50",
            source_url="https://github.com/microsoft/playwright/releases/tag/v1.50.0",
            source_name="Playwright Release Notes",
            topic_key="playwright-1.50",
        )
        adapter = FakeSourceCollectorAdapter({"playwright-release-notes": [item]})
        result = collect_for_role("qa-engineer", adapter=adapter)
        self.assertEqual(len(result.accepted), 1)
        self.assertTrue(result.accepted[0].dedup_key)
        self.assertTrue(result.accepted[0].dedup_key.startswith("eng-knowledge:"))


class SameDayTopicGuardTests(unittest.TestCase):
    def test_same_day_same_topic_blocked(self) -> None:
        already = [
            _item(
                role="ai-engineer",
                title="OpenAI ships X",
                source_url="https://openai.com/news/x",
                source_name="OpenAI News & Research",
                topic_key="openai-x",
                collected_at="2026-05-08T01:00:00Z",
            )
        ]
        new_item = _item(
            role="ai-engineer",
            title="OpenAI ships X (later)",
            source_url="https://openai.com/news/x-different",
            source_name="OpenAI News & Research",
            topic_key="openai-x",
            collected_at="2026-05-08T05:00:00Z",
        )
        adapter = FakeSourceCollectorAdapter({"openai-news": [new_item]})
        result = collect_for_role(
            "ai-engineer", adapter=adapter, already_stored=already
        )
        self.assertEqual(len(result.accepted), 0)
        reasons = [r["reason"] for r in result.rejected]
        self.assertIn("same_day_topic_already_stored", reasons)


class UtcHelperTests(unittest.TestCase):
    def test_utc_now_iso_returns_iso_zulu(self) -> None:
        ts = utc_now_iso()
        self.assertTrue(ts.endswith("Z"))
        self.assertEqual(len(ts), 20)


if __name__ == "__main__":
    unittest.main()
