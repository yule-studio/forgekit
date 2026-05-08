"""Collector adapter interface + offline orchestration.

Contract:

  * Each :class:`SourceCollectorAdapter` knows how to turn a single
    :class:`SourceEntry` into a tuple of
    :class:`EngineeringKnowledgeItem`.
  * :class:`FakeSourceCollectorAdapter` is the offline default — it
    reads from an in-memory mapping ``source_id → items``. Tests use
    it; production swaps in real RSS / sitemap / GitHub API
    adapters.
  * :func:`collect_for_role` calls the adapter on every
    auto-collectable source for the role, applies dedup, sorts by
    ``(tier, importance, freshness)`` so Tier 1 / critical items win
    the daily slot, then truncates to the role's daily limit.

Strictly offline. Live adapters live in a follow-up — this module
defines the seam they plug into.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .dedup import (
    compute_dedup_key,
    dedup_items,
    enforce_same_day_topic_uniqueness,
)
from .models import (
    EngineeringKnowledgeItem,
    Importance,
    SourceEntry,
    SourceTier,
)
from .source_registry import (
    auto_collectable_sources,
    daily_limit_for_role,
    role_sources,
)


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------


class SourceCollectorAdapter(Protocol):
    """A single function-of-shape that maps one source → items.

    Subprotocols (RSS / sitemap / github_api / manual) implement this
    by translating the external transport into a tuple of
    :class:`EngineeringKnowledgeItem`. Implementations are expected
    to:

      * NOT block the event loop for more than a few seconds per
        source — the orchestrator may run several adapters
        sequentially.
      * NOT raise — return an empty tuple on transport failure and
        log via the supplied ``warn`` callback.
    """

    def __call__(self, source: SourceEntry) -> Tuple[EngineeringKnowledgeItem, ...]:
        ...


# ---------------------------------------------------------------------------
# Fake adapter — the offline default
# ---------------------------------------------------------------------------


class FakeSourceCollectorAdapter:
    """In-memory adapter for tests.

    Constructed with a mapping ``source_id → items``. ``__call__``
    returns the items for a given source, defaulting to an empty
    tuple. Records every call in :attr:`calls` so tests can assert
    the orchestrator visited exactly the auto-collectable sources.
    """

    def __init__(
        self, payload: Mapping[str, Sequence[EngineeringKnowledgeItem]]
    ) -> None:
        self._payload = {
            source_id: tuple(items) for source_id, items in payload.items()
        }
        self.calls: List[str] = []

    def __call__(
        self, source: SourceEntry
    ) -> Tuple[EngineeringKnowledgeItem, ...]:
        self.calls.append(source.source_id)
        return self._payload.get(source.source_id, ())


# ---------------------------------------------------------------------------
# Orchestration result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionRunResult:
    """Output of :func:`collect_for_role`."""

    role: str
    accepted: Tuple[EngineeringKnowledgeItem, ...]
    rejected: Tuple[Mapping[str, Any], ...]
    visited_source_ids: Tuple[str, ...]
    daily_limit: int

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "role": self.role,
            "accepted_count": len(self.accepted),
            "rejected_count": len(self.rejected),
            "visited_source_ids": list(self.visited_source_ids),
            "daily_limit": self.daily_limit,
            "accepted": [item.to_payload() for item in self.accepted],
            "rejected": [dict(r) for r in self.rejected],
        }


# ---------------------------------------------------------------------------
# Sorting helpers
# ---------------------------------------------------------------------------


_TIER_ORDER: Mapping[SourceTier, int] = {
    SourceTier.TIER_1: 0,
    SourceTier.TIER_2: 1,
    SourceTier.TIER_3: 2,
    SourceTier.TIER_4: 3,
}


_IMPORTANCE_ORDER: Mapping[Importance, int] = {
    Importance.CRITICAL: 0,
    Importance.HIGH: 1,
    Importance.MEDIUM: 2,
    Importance.LOW: 3,
}


def _source_lookup(role_id: str) -> Mapping[str, SourceEntry]:
    return {entry.source_id: entry for entry in role_sources(role_id)}


def _sort_key(
    item: EngineeringKnowledgeItem,
    *,
    sources: Mapping[str, SourceEntry],
) -> Tuple[int, int, float, str, str]:
    # Look up the source by name (we store ``source_name`` on the
    # item, but the registry keys on ``source_id``). Fall back to a
    # source whose ``name`` matches if exact id lookup fails — tests
    # often craft items with a synthetic ``source_name`` instead of
    # plumbing the full id through.
    found: Optional[SourceEntry] = None
    for entry in sources.values():
        if entry.name == item.source_name or entry.source_id == item.source_name:
            found = entry
            break
    tier_rank = _TIER_ORDER.get(found.tier, 4) if found else 4
    importance_rank = _IMPORTANCE_ORDER.get(item.importance, 4)
    freshness = (found.freshness_weight if found else 0.5) * (
        found.trust_weight if found else 0.5
    )
    # Negate freshness so larger means earlier in sort.
    return (
        tier_rank,
        importance_rank,
        -freshness,
        item.collected_at,
        item.title,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def collect_for_role(
    role_id: str,
    *,
    adapter: SourceCollectorAdapter,
    already_stored: Sequence[EngineeringKnowledgeItem] = (),
    daily_limit: Optional[int] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> CollectionRunResult:
    """Run a daily collection sweep for *role_id*.

    Behaviour:

      1. Pull the role's auto-collectable source list.
      2. Call *adapter* for each source. Collect produced items into a
         single bag.
      3. Stamp ``dedup_key`` on items missing one.
      4. Apply :func:`dedup_items` (URL / title / topic / key).
      5. Apply :func:`enforce_same_day_topic_uniqueness` against
         *already_stored*.
      6. Sort by ``(tier, importance, trust*freshness)`` and truncate
         to *daily_limit* (default 5).
      7. Return :class:`CollectionRunResult` so the caller can audit
         which items were dropped and why.
    """

    if warn is None:
        warn = lambda _msg: None  # noqa: E731 — tiny shim

    auto_sources = auto_collectable_sources(role_id)
    visited: List[str] = []
    bag: List[EngineeringKnowledgeItem] = []
    for source in auto_sources:
        visited.append(source.source_id)
        try:
            produced = adapter(source) or ()
        except Exception as exc:  # noqa: BLE001 — adapter must not crash run
            warn(
                f"adapter failed for source={source.source_id!r}: "
                f"{type(exc).__name__}"
            )
            produced = ()
        for item in produced:
            if not item.dedup_key:
                item = item.with_dedup_key(compute_dedup_key(item))
            bag.append(item)

    deduped, dedup_rejected = dedup_items(bag)
    fresh, same_day_rejected = enforce_same_day_topic_uniqueness(
        deduped, already_stored=already_stored
    )

    sources_by_id = _source_lookup(role_id)
    ordered = tuple(
        sorted(fresh, key=lambda item: _sort_key(item, sources=sources_by_id))
    )

    limit = daily_limit if daily_limit is not None else daily_limit_for_role(role_id)
    accepted = ordered[: max(0, limit)]
    overflow = ordered[max(0, limit):]
    overflow_rejections: List[Mapping[str, Any]] = [
        {
            "reason": "daily_limit_exceeded",
            "topic_key": item.topic_key,
            "title": item.title,
        }
        for item in overflow
    ]

    rejected = list(dedup_rejected) + list(same_day_rejected) + overflow_rejections

    return CollectionRunResult(
        role=role_id,
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        visited_source_ids=tuple(visited),
        daily_limit=limit,
    )


def utc_now_iso() -> str:
    """Helper: ISO-8601 UTC now. Exposed so tests share the format."""

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "CollectionRunResult",
    "FakeSourceCollectorAdapter",
    "SourceCollectorAdapter",
    "collect_for_role",
    "utc_now_iso",
]
