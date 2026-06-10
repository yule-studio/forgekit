"""LongTermMemory facade + build_memory_pack (F10 / #101).

Single entrypoint a worker uses to retrieve a deduplicated, ranked
:class:`MemoryPack` for a given request. Composes:

  * Fanout: ``LongTermMemory.for_topic / for_role / for_issue /
    recent_decisions`` query every registered :class:`MemorySource`.
  * Rank: :class:`RelevanceSelector` scores each shard. BLOCK
    mistake shards are pinned at the top regardless of score.
  * Dedupe: shards with identical ``hash`` are merged — the highest-
    scoring instance wins.

Hard rails:

  * ``env YULE_LONG_TERM_MEMORY_ENABLED=false`` → :func:`build_memory_pack`
    returns an empty pack immediately (no source query, no scoring).
    This is the "회로 단절 안전" rail from issue #101.
  * No source is invoked twice for the same filter — each method
    builds a filter once and dispatches once per source.
  * Source ``query`` exceptions are isolated: a misbehaving adapter
    cannot block other adapters' shards from surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import (
    DEFAULT_MAX_SHARDS_PER_QUERY,
    MemoryFilter,
    MemoryPack,
    MemoryShard,
    MemorySource,
    RequestContext,
    ShardKind,
    _query_signature,
    _utc_now_iso,
    long_term_memory_enabled,
    max_shards_per_query,
)
from .relevance_selector import RelevanceSelector


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class LongTermMemory:
    """Facade over an ordered list of :class:`MemorySource` adapters.

    Construct with whichever subset of sources is wired in the
    operator's runtime. Methods are read-only — each call dispatches
    fresh queries; no result caching happens here so adapters control
    their own freshness story.
    """

    def __init__(self, sources: Sequence[MemorySource]) -> None:
        self._sources: Tuple[MemorySource, ...] = tuple(sources)

    @property
    def sources(self) -> Tuple[MemorySource, ...]:
        return self._sources

    # ------------------------------------------------------------------
    # Convenience fanout methods
    # ------------------------------------------------------------------

    def for_topic(
        self,
        topic_tags: Sequence[str],
        *,
        limit: int = DEFAULT_MAX_SHARDS_PER_QUERY,
    ) -> Tuple[MemoryShard, ...]:
        return self._fanout(
            MemoryFilter(topic_tags=tuple(topic_tags), limit=limit)
        )

    def for_role(
        self,
        role: str,
        *,
        limit: int = DEFAULT_MAX_SHARDS_PER_QUERY,
    ) -> Tuple[MemoryShard, ...]:
        return self._fanout(MemoryFilter(role=role, limit=limit))

    def for_issue(
        self,
        issue: int,
        *,
        limit: int = DEFAULT_MAX_SHARDS_PER_QUERY,
    ) -> Tuple[MemoryShard, ...]:
        return self._fanout(MemoryFilter(issue=issue, limit=limit))

    def recent_decisions(
        self,
        *,
        limit: int = DEFAULT_MAX_SHARDS_PER_QUERY,
        since: Optional[str] = None,
    ) -> Tuple[MemoryShard, ...]:
        """Return shards from DECISION-kind sources only.

        Other kinds are filtered out so a caller looking for "what
        did we already decide?" sees the audit trail without noise
        from notes / mistakes.
        """

        filter_ = MemoryFilter(limit=limit, since=since)
        out: List[MemoryShard] = []
        for source in self._sources:
            if getattr(source, "kind", None) != ShardKind.DECISION:
                continue
            for shard in _safe_query(source, filter_):
                out.append(shard)
        return tuple(out)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fanout(self, filter: MemoryFilter) -> Tuple[MemoryShard, ...]:
        out: List[MemoryShard] = []
        seen_hashes: set = set()
        for source in self._sources:
            for shard in _safe_query(source, filter):
                if shard.hash in seen_hashes:
                    continue
                seen_hashes.add(shard.hash)
                out.append(shard)
        return tuple(out)


# ---------------------------------------------------------------------------
# build_memory_pack
# ---------------------------------------------------------------------------


def build_memory_pack(
    *,
    long_term_memory: Optional[LongTermMemory],
    request_context: RequestContext,
    limit: int = DEFAULT_MAX_SHARDS_PER_QUERY,
    selector: Optional[RelevanceSelector] = None,
) -> MemoryPack:
    """Compose a ranked, deduplicated :class:`MemoryPack`.

    Returns an empty pack when:

      * ``long_term_memory`` is None (no adapter wired), OR
      * ``YULE_LONG_TERM_MEMORY_ENABLED`` is false / unset.

    Otherwise queries the union of (role + topic + issue) filters,
    deduplicates by ``hash`` (best score wins), pins BLOCK mistakes
    to the top, then returns the top *limit* shards.
    """

    signature = _query_signature(
        request_context=request_context,
        limit=limit,
    )
    if long_term_memory is None or not long_term_memory_enabled():
        return MemoryPack(
            shards=(),
            generated_at=_utc_now_iso(),
            query_signature=signature,
        )

    effective_selector = selector or RelevanceSelector()
    per_source_cap = max_shards_per_query()

    # Build a union filter — request context tells us all three axes
    # at once.
    filter_ = MemoryFilter(
        role=request_context.role,
        topic_tags=tuple(request_context.topic_tags),
        issue=request_context.issue,
        pr=request_context.pr,
        limit=per_source_cap,
    )

    scored_by_hash: Dict[str, Tuple[float, MemoryShard]] = {}
    for source in long_term_memory.sources:
        for shard in _safe_query(source, filter_):
            score = effective_selector.score(
                shard, request_context=request_context
            )
            existing = scored_by_hash.get(shard.hash)
            if existing is None or score > existing[0]:
                scored_by_hash[shard.hash] = (score, shard)

    # Sort: BLOCK mistakes first, then by score desc, then by
    # created_at desc, then by hash for total determinism.
    def _sort_key(entry: Tuple[float, MemoryShard]) -> Tuple[int, float, str, str]:
        score, shard = entry
        block_priority = 0
        if (
            shard.kind == ShardKind.MISTAKE
            and (shard.blocker_level or "").upper() == "BLOCK"
        ):
            block_priority = -1  # smaller = earlier
        # Negate score so larger ends up first.
        return (block_priority, -score, _invert(shard.created_at), shard.hash)

    sorted_entries = sorted(scored_by_hash.values(), key=_sort_key)
    bounded = sorted_entries[: max(0, int(limit))]
    shards = tuple(shard for _, shard in bounded)

    return MemoryPack(
        shards=shards,
        generated_at=_utc_now_iso(),
        query_signature=signature,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_query(source: MemorySource, filter: MemoryFilter) -> Iterable[MemoryShard]:
    try:
        return tuple(source.query(filter))
    except Exception:  # noqa: BLE001 - isolate adapter faults
        return ()


def _invert(value: str) -> str:
    """Return a sort key such that newer ISO timestamps sort earlier.

    Python sorts ASCII descending naturally with reverse=True, but
    we want a single composite key. Inverting via a max-character
    placeholder yields the same effect within tuple-sort ordering.
    """

    if not value:
        return "\uffff"
    # Use a "complementary" string: pad with a high codepoint so
    # later (lexicographically larger) ISO timestamps end up earlier
    # when sorted ascending.
    return "".join(chr(0x10FFFF - min(ord(c), 0x10FFFF)) for c in value)


__all__ = (
    "LongTermMemory",
    "build_memory_pack",
)
