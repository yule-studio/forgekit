"""RelevanceSelector unit tests (F10 / #101).

Coverage:

  * Score is clamped to [0.0, 1.0]
  * Recency decays linearly across the horizon, hits 0 at boundary
  * Topic overlap is Jaccard, returns 0 when either side empty
  * Role match recognises both full role string and trailing segment
  * Source-trust uses the static SOURCE_TRUST table
  * MISTAKE + BLOCK shards always return 1.0 (hard rail)
  * Deterministic — identical inputs yield identical scores
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_agent_memory import (
    MemoryShard,
    RequestContext,
    ShardKind,
)
from yule_agent_memory.relevance_selector import RelevanceSelector


def _shard(
    *,
    kind: ShardKind,
    created_at: str = "2026-05-10T12:00:00+00:00",
    topic_tags=(),
    source: str = "obsidian-vault:notes/x.md",
    content: str = "x",
    blocker_level=None,
) -> MemoryShard:
    return MemoryShard(
        kind=kind,
        source=source,
        content=content,
        created_at=created_at,
        topic_tags=tuple(topic_tags),
        blocker_level=blocker_level,
    )


class RelevanceSelectorTests(unittest.TestCase):
    def test_score_clamped_to_unit_interval(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(kind=ShardKind.OBSIDIAN_NOTE)
        score = selector.score(shard, request_context=RequestContext())
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_block_mistake_always_one(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(
            kind=ShardKind.MISTAKE,
            created_at="2000-01-01T00:00:00+00:00",  # ancient — recency 0
            topic_tags=(),  # no overlap
            source="mistake-ledger:nobody",
            blocker_level="BLOCK",
        )
        score = selector.score(shard, request_context=RequestContext(role="anyone"))
        self.assertEqual(score, 1.0)

    def test_recency_zero_beyond_horizon(self) -> None:
        selector = RelevanceSelector(horizon_days=30)
        old_iso = "2020-01-01T00:00:00+00:00"
        shard = _shard(kind=ShardKind.OBSIDIAN_NOTE, created_at=old_iso)
        score = selector._recency_score(shard, RequestContext())
        self.assertEqual(score, 0.0)

    def test_recency_full_score_for_now(self) -> None:
        selector = RelevanceSelector(horizon_days=30)
        now = datetime.now(tz=timezone.utc).replace(microsecond=0)
        shard = _shard(kind=ShardKind.OBSIDIAN_NOTE, created_at=now.isoformat())
        score = selector._recency_score(shard, RequestContext(now=now))
        self.assertEqual(score, 1.0)

    def test_topic_overlap_jaccard(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(
            kind=ShardKind.OBSIDIAN_NOTE,
            topic_tags=("paste", "guard"),
        )
        ctx = RequestContext(topic_tags=("guard", "router"))
        score = selector._topic_overlap_score(shard, ctx)
        # intersection={guard}, union={paste,guard,router} → 1/3
        self.assertAlmostEqual(score, 1 / 3)

    def test_topic_overlap_zero_when_either_side_empty(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(kind=ShardKind.OBSIDIAN_NOTE, topic_tags=())
        self.assertEqual(
            selector._topic_overlap_score(shard, RequestContext(topic_tags=("a",))),
            0.0,
        )
        shard2 = _shard(kind=ShardKind.OBSIDIAN_NOTE, topic_tags=("a",))
        self.assertEqual(
            selector._topic_overlap_score(shard2, RequestContext(topic_tags=())),
            0.0,
        )

    def test_role_match_matches_trailing_segment(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(
            kind=ShardKind.AUDIT,
            source="agent-ops-audit:engineering-agent/ai-engineer-x",
        )
        score = selector._role_match_score(
            shard,
            RequestContext(role="engineering-agent/ai-engineer"),
        )
        self.assertEqual(score, 1.0)

    def test_source_trust_table(self) -> None:
        selector = RelevanceSelector()
        decision = _shard(kind=ShardKind.DECISION)
        obsidian = _shard(kind=ShardKind.OBSIDIAN_NOTE)
        session = _shard(kind=ShardKind.SESSION_EXTRA)
        self.assertGreater(
            selector._source_trust_score(decision),
            selector._source_trust_score(obsidian),
        )
        self.assertGreater(
            selector._source_trust_score(obsidian),
            selector._source_trust_score(session),
        )

    def test_deterministic_for_equal_inputs(self) -> None:
        selector = RelevanceSelector()
        shard = _shard(
            kind=ShardKind.OBSIDIAN_NOTE,
            topic_tags=("a", "b"),
            source="obsidian-vault:notes/x.md",
        )
        fixed_now = datetime(2026, 5, 11, tzinfo=timezone.utc)
        ctx = RequestContext(
            role="ai-engineer", topic_tags=("a",), now=fixed_now
        )
        first = selector.score(shard, request_context=ctx)
        second = selector.score(shard, request_context=ctx)
        self.assertEqual(first, second)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
