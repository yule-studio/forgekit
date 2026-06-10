"""Dedup — URL / title / topic / dedup-key collapse + same-day topic guard."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.engineering_intelligence.dedup import (
    compute_dedup_key,
    dedup_items,
    enforce_same_day_topic_uniqueness,
)
from yule_engineering.agents.engineering_intelligence.models import (
    EngineeringKnowledgeItem,
    SourceKind,
)


def _it(
    *,
    role: str = "backend-engineer",
    title: str = "Title",
    topic_key: str = "topic",
    source_url: str = "https://example.com/a",
    collected_at: str = "2026-05-08T00:00:00Z",
) -> EngineeringKnowledgeItem:
    return EngineeringKnowledgeItem(
        item_id=f"{role}-{title}",
        topic_key=topic_key,
        title=title,
        role=role,
        stack_tags=("x",),
        source_name="src",
        source_url=source_url,
        source_kind=SourceKind.DOCS,
        collected_at=collected_at,
    )


class ComputeDedupKeyTests(unittest.TestCase):
    def test_same_inputs_produce_same_key(self) -> None:
        a = _it(title="React 19", topic_key="react-19")
        b = _it(title="React 19", topic_key="react-19")
        self.assertEqual(compute_dedup_key(a), compute_dedup_key(b))

    def test_url_normalisation_collapses_tracking_params(self) -> None:
        a = _it(source_url="https://example.com/a/?utm_source=feed&utm_medium=rss")
        b = _it(source_url="https://example.com/a/")
        self.assertEqual(compute_dedup_key(a), compute_dedup_key(b))

    def test_url_fragment_ignored(self) -> None:
        a = _it(source_url="https://example.com/a/#section1")
        b = _it(source_url="https://example.com/a/")
        self.assertEqual(compute_dedup_key(a), compute_dedup_key(b))

    def test_title_normalisation_collapses_whitespace(self) -> None:
        a = _it(title="  React 19   RC ")
        b = _it(title="react 19 rc")
        self.assertEqual(compute_dedup_key(a), compute_dedup_key(b))

    def test_role_prefix_present(self) -> None:
        key = compute_dedup_key(_it(role="ai-engineer", title="x"))
        self.assertTrue(key.startswith("eng-knowledge:ai-engineer:"))


class DedupItemsTests(unittest.TestCase):
    def test_url_collision_drops_second(self) -> None:
        a = _it(source_url="https://example.com/a", title="A", topic_key="t-a")
        b = _it(source_url="https://example.com/a", title="A mirror", topic_key="t-a-mirror")
        kept, rejected = dedup_items([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected[0]["reason"], "url_collision")

    def test_topic_collision_drops_second(self) -> None:
        a = _it(source_url="https://example.com/a", title="A", topic_key="topic")
        b = _it(source_url="https://example.com/b", title="B", topic_key="topic")
        kept, rejected = dedup_items([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected[0]["reason"], "topic_collision")

    def test_title_collision_drops_second(self) -> None:
        a = _it(source_url="https://example.com/a", title="React 19 RC", topic_key="t1")
        b = _it(source_url="https://example.com/b", title="React 19 RC", topic_key="t2")
        kept, rejected = dedup_items([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected[0]["reason"], "title_collision")

    def test_dedup_key_attached_when_missing(self) -> None:
        a = _it()
        kept, _ = dedup_items([a])
        self.assertTrue(kept[0].dedup_key)


class SameDayTopicTests(unittest.TestCase):
    def test_already_stored_blocks(self) -> None:
        already = [_it(topic_key="t1", collected_at="2026-05-08T01:00:00Z")]
        new = _it(
            topic_key="t1",
            source_url="https://example.com/different",
            collected_at="2026-05-08T05:00:00Z",
        )
        kept, rejected = enforce_same_day_topic_uniqueness(
            [new], already_stored=already
        )
        self.assertEqual(kept, ())
        self.assertEqual(rejected[0]["reason"], "same_day_topic_already_stored")

    def test_different_day_allowed(self) -> None:
        already = [_it(topic_key="t1", collected_at="2026-05-07T01:00:00Z")]
        new = _it(topic_key="t1", collected_at="2026-05-08T01:00:00Z")
        kept, rejected = enforce_same_day_topic_uniqueness(
            [new], already_stored=already
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, ())

    def test_same_day_duplicate_in_batch(self) -> None:
        a = _it(topic_key="t1", collected_at="2026-05-08T01:00:00Z")
        b = _it(
            topic_key="t1",
            source_url="https://example.com/other",
            collected_at="2026-05-08T05:00:00Z",
        )
        kept, rejected = enforce_same_day_topic_uniqueness([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(
            rejected[0]["reason"], "same_day_topic_duplicate_in_batch"
        )


if __name__ == "__main__":
    unittest.main()
