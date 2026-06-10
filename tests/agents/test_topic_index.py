"""TopicIndex unit tests (F10 / #101).

Coverage:

  * Empty / single / multi shard add + lookup
  * Token normalisation (case insensitive, ASCII tokenisation)
  * Content head budget honoured (long content tail does not pollute)
  * Idempotent re-add by hash
  * Deterministic ordering (created_at desc, hash asc tiebreak)
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_memory import MemoryShard, ShardKind
from yule_agent_memory.topic_index import TopicIndex


def _shard(
    *,
    content: str,
    created_at: str = "2026-05-10T12:00:00+00:00",
    topic_tags=(),
    kind: ShardKind = ShardKind.OBSIDIAN_NOTE,
    source: str = "obsidian-vault:note.md",
) -> MemoryShard:
    return MemoryShard(
        kind=kind,
        source=source,
        content=content,
        created_at=created_at,
        topic_tags=tuple(topic_tags),
    )


class TopicIndexTests(unittest.TestCase):
    def test_empty_index_returns_no_results(self) -> None:
        index = TopicIndex()
        self.assertEqual(index.lookup(["anything"]), ())
        self.assertEqual(len(index), 0)

    def test_lookup_by_tag_matches(self) -> None:
        shard = _shard(content="hello", topic_tags=("paste-guard", "f1"))
        index = TopicIndex.from_shards([shard])
        result = index.lookup(["paste-guard"])
        self.assertEqual(result, (shard,))

    def test_lookup_is_case_insensitive(self) -> None:
        shard = _shard(content="hello", topic_tags=("PasteGuard",))
        index = TopicIndex.from_shards([shard])
        result = index.lookup(["pasteguard"])
        self.assertEqual(result, (shard,))

    def test_lookup_returns_empty_for_unknown_token(self) -> None:
        shard = _shard(content="known", topic_tags=("known",))
        index = TopicIndex.from_shards([shard])
        self.assertEqual(index.lookup(["unrelated"]), ())

    def test_lookup_ignores_empty_tokens(self) -> None:
        shard = _shard(content="x", topic_tags=("t",))
        index = TopicIndex.from_shards([shard])
        self.assertEqual(index.lookup(["", "  ", None]), ())  # type: ignore[list-item]

    def test_content_tokens_indexed_within_budget(self) -> None:
        long_tail = " ".join(["needle"] + ["filler"] * 50 + ["tail"])
        shard = _shard(content=long_tail, topic_tags=())
        index = TopicIndex.from_shards([shard], content_token_budget=5)
        self.assertEqual(index.lookup(["needle"]), (shard,))
        # "tail" is beyond budget — should not match.
        self.assertEqual(index.lookup(["tail"]), ())

    def test_idempotent_re_add(self) -> None:
        shard = _shard(content="alpha", topic_tags=("a",))
        index = TopicIndex()
        index.add(shard)
        index.add(shard)
        self.assertEqual(len(index), 1)
        self.assertEqual(index.lookup(["alpha"]), (shard,))

    def test_deterministic_ordering_by_created_at_desc(self) -> None:
        older = _shard(content="old", created_at="2025-01-01T00:00:00+00:00", topic_tags=("z",))
        newer = _shard(content="new", created_at="2026-05-10T00:00:00+00:00", topic_tags=("z",))
        index = TopicIndex.from_shards([older, newer])
        result = index.lookup(["z"])
        self.assertEqual(result, (newer, older))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
