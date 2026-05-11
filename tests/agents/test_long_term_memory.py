"""LongTermMemory + build_memory_pack tests (F10 / #101).

Covers:

  * `build_memory_pack` returns empty pack when env OFF
  * `build_memory_pack` returns ranked, deduped pack when env ON
  * BLOCK mistake shard pinned to MemoryPack.shards[0]
  * Per-source fanout cap honoured
  * Adapter exception isolated — one bad source does not block others
  * for_topic / for_role / for_issue dispatch the right filter
  * recent_decisions filters to DECISION kind only
  * query_signature stable for identical request_context
"""

from __future__ import annotations

import os
import unittest
from typing import Iterable, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.memory import (
    ENV_LONG_TERM_MEMORY_ENABLED,
    LongTermMemory,
    MemoryFilter,
    MemoryPack,
    MemoryShard,
    MemorySource,
    RequestContext,
    ShardKind,
    build_memory_pack,
)


class _FakeSource:
    """Minimal MemorySource implementation for unit tests."""

    def __init__(
        self,
        kind: ShardKind,
        shards: Sequence[MemoryShard],
        *,
        raise_on_query: bool = False,
    ) -> None:
        self.kind = kind
        self._shards = tuple(shards)
        self._raise = raise_on_query
        self.calls: list = []

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:
        self.calls.append(filter)
        if self._raise:
            raise RuntimeError("boom")
        return self._shards


def _shard(
    *,
    kind: ShardKind = ShardKind.OBSIDIAN_NOTE,
    content: str,
    source: str = "fake-source",
    created_at: str = "2026-05-10T12:00:00+00:00",
    topic_tags=(),
    related_issue=None,
    blocker_level=None,
) -> MemoryShard:
    return MemoryShard(
        kind=kind,
        source=source,
        content=content,
        created_at=created_at,
        topic_tags=tuple(topic_tags),
        related_issue=related_issue,
        blocker_level=blocker_level,
    )


class LongTermMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Test enable / disable via env var; ensure cleanup.
        self._prev_env = os.environ.get(ENV_LONG_TERM_MEMORY_ENABLED)
        os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = "true"

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop(ENV_LONG_TERM_MEMORY_ENABLED, None)
        else:
            os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = self._prev_env

    # ------------------------------------------------------------------
    # build_memory_pack
    # ------------------------------------------------------------------

    def test_empty_pack_when_env_disabled(self) -> None:
        os.environ[ENV_LONG_TERM_MEMORY_ENABLED] = "false"
        source = _FakeSource(
            ShardKind.OBSIDIAN_NOTE,
            [_shard(content="x")],
        )
        ltm = LongTermMemory([source])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(role="ai-engineer"),
        )
        self.assertIsInstance(pack, MemoryPack)
        self.assertEqual(pack.shards, ())
        # source never queried
        self.assertEqual(source.calls, [])

    def test_empty_pack_when_long_term_memory_none(self) -> None:
        pack = build_memory_pack(
            long_term_memory=None,
            request_context=RequestContext(),
        )
        self.assertEqual(pack.shards, ())

    def test_pack_dedupes_by_hash(self) -> None:
        shard_a = _shard(
            kind=ShardKind.OBSIDIAN_NOTE,
            content="same content",
            source="fake-source",
            topic_tags=("topic",),
        )
        shard_b = _shard(
            kind=ShardKind.OBSIDIAN_NOTE,
            content="same content",
            source="fake-source",
            topic_tags=("topic",),
        )
        src1 = _FakeSource(ShardKind.OBSIDIAN_NOTE, [shard_a])
        src2 = _FakeSource(ShardKind.OBSIDIAN_NOTE, [shard_b])
        ltm = LongTermMemory([src1, src2])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(topic_tags=("topic",)),
        )
        self.assertEqual(len(pack.shards), 1)

    def test_block_mistake_pinned_to_top(self) -> None:
        recent_note = _shard(
            kind=ShardKind.OBSIDIAN_NOTE,
            content="fresh & topical",
            topic_tags=("topic",),
            created_at="2026-05-11T00:00:00+00:00",
        )
        ancient_block = _shard(
            kind=ShardKind.MISTAKE,
            content="[force_push] protected branch",
            source="mistake-ledger:ai-engineer",
            created_at="2020-01-01T00:00:00+00:00",  # very old
            topic_tags=(),
            blocker_level="BLOCK",
        )
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, [recent_note, ancient_block])
        ltm = LongTermMemory([src])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(topic_tags=("topic",)),
            limit=5,
        )
        self.assertGreater(len(pack.shards), 0)
        self.assertEqual(pack.shards[0].kind, ShardKind.MISTAKE)
        self.assertEqual((pack.shards[0].blocker_level or "").upper(), "BLOCK")

    def test_source_exception_isolated(self) -> None:
        good = _FakeSource(
            ShardKind.OBSIDIAN_NOTE,
            [_shard(content="good", topic_tags=("topic",))],
        )
        bad = _FakeSource(
            ShardKind.AUDIT, [_shard(content="bad")], raise_on_query=True
        )
        ltm = LongTermMemory([bad, good])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(topic_tags=("topic",)),
        )
        self.assertGreaterEqual(len(pack.shards), 1)
        contents = {s.content for s in pack.shards}
        self.assertIn("good", contents)

    def test_query_signature_stable_for_equal_context(self) -> None:
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, [])
        ltm = LongTermMemory([src])
        ctx = RequestContext(role="ai-engineer", topic_tags=("a", "b"), issue=101)
        first = build_memory_pack(long_term_memory=ltm, request_context=ctx)
        second = build_memory_pack(long_term_memory=ltm, request_context=ctx)
        self.assertEqual(first.query_signature, second.query_signature)

    def test_limit_caps_pack_size(self) -> None:
        shards = [
            _shard(
                content=f"s{i}",
                topic_tags=("topic",),
                source=f"fake-source:{i}",
                created_at=f"2026-05-{i+1:02d}T00:00:00+00:00",
            )
            for i in range(8)
        ]
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, shards)
        ltm = LongTermMemory([src])
        pack = build_memory_pack(
            long_term_memory=ltm,
            request_context=RequestContext(topic_tags=("topic",)),
            limit=3,
        )
        self.assertEqual(len(pack.shards), 3)

    # ------------------------------------------------------------------
    # LongTermMemory convenience methods
    # ------------------------------------------------------------------

    def test_for_topic_dispatches_topic_filter(self) -> None:
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, [])
        ltm = LongTermMemory([src])
        ltm.for_topic(["alpha"], limit=4)
        self.assertEqual(len(src.calls), 1)
        self.assertEqual(src.calls[0].topic_tags, ("alpha",))
        self.assertEqual(src.calls[0].limit, 4)

    def test_for_role_dispatches_role_filter(self) -> None:
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, [])
        ltm = LongTermMemory([src])
        ltm.for_role("ai-engineer", limit=2)
        self.assertEqual(src.calls[0].role, "ai-engineer")

    def test_for_issue_dispatches_issue_filter(self) -> None:
        src = _FakeSource(ShardKind.OBSIDIAN_NOTE, [])
        ltm = LongTermMemory([src])
        ltm.for_issue(101)
        self.assertEqual(src.calls[0].issue, 101)

    def test_recent_decisions_filters_to_decision_kind(self) -> None:
        decision_src = _FakeSource(
            ShardKind.DECISION, [_shard(kind=ShardKind.DECISION, content="d")]
        )
        note_src = _FakeSource(
            ShardKind.OBSIDIAN_NOTE, [_shard(content="note")]
        )
        ltm = LongTermMemory([decision_src, note_src])
        result = ltm.recent_decisions(limit=10)
        # Only decision_src is queried, only DECISION shards returned.
        self.assertEqual(len(note_src.calls), 0)
        self.assertEqual(len(decision_src.calls), 1)
        self.assertTrue(all(s.kind == ShardKind.DECISION for s in result))

    def test_fanout_dedupes_across_sources(self) -> None:
        shard = _shard(
            content="same",
            source="fake-source",
            topic_tags=("topic",),
        )
        src1 = _FakeSource(ShardKind.OBSIDIAN_NOTE, [shard])
        src2 = _FakeSource(ShardKind.OBSIDIAN_NOTE, [shard])
        ltm = LongTermMemory([src1, src2])
        result = ltm.for_topic(["topic"])
        self.assertEqual(len(result), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
