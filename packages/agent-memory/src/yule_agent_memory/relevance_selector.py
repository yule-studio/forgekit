"""Relevance scoring for memory shards (F10 / #101).

Deterministic, dependency-free ranker. Four components combine via
weighted average into a single 0..1 score:

  1. **recency** — exponential decay against the env-configured
     freshness window (default 30 days). Shards with malformed or
     missing ``created_at`` get 0.0 so a poorly-shaped source can
     never dominate.
  2. **topic_overlap** — Jaccard similarity between the shard's
     ``topic_tags`` and the request's ``topic_tags``. Returns 0.0
     when either set is empty.
  3. **role_match** — 1.0 when the shard's source string contains
     the request role (lower-case substring match), else 0.0. Most
     sources prefix the source identifier with a role name so the
     match is high-precision.
  4. **source_trust** — the static table in :data:`SOURCE_TRUST`,
     with one override: mistake shards at ``BLOCK`` blocker level
     surface at 1.0 so the preflight signal cannot be drowned out.

Default weights (sum to 1.0):

  ``recency=0.25, topic_overlap=0.35, role_match=0.15, source_trust=0.25``

Callers can pass custom weights for experimentation but
:meth:`RelevanceSelector.score` always clamps the final value into
[0.0, 1.0] so downstream callers can compare shards confidently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from . import (
    MemoryShard,
    RequestContext,
    ShardKind,
    SOURCE_TRUST,
    freshness_days,
    tokenize,
)


# ---------------------------------------------------------------------------
# Default weights
# ---------------------------------------------------------------------------


DEFAULT_WEIGHTS: Mapping[str, float] = {
    "recency": 0.25,
    "topic_overlap": 0.35,
    "role_match": 0.15,
    "source_trust": 0.25,
}


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelevanceSelector:
    """Deterministic 4-component relevance scorer.

    Construct with default weights (:data:`DEFAULT_WEIGHTS`) unless
    an experiment is in flight. The class is frozen so callers can
    share one instance across threads without surprise mutation.
    """

    weights: Mapping[str, float] = None  # type: ignore[assignment]
    horizon_days: Optional[int] = None

    def __post_init__(self) -> None:
        if self.weights is None:
            object.__setattr__(self, "weights", dict(DEFAULT_WEIGHTS))
        if self.horizon_days is None:
            object.__setattr__(self, "horizon_days", freshness_days())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        shard: MemoryShard,
        *,
        request_context: RequestContext,
    ) -> float:
        """Return the [0.0, 1.0] relevance score for *shard*.

        Special case: a mistake shard with ``blocker_level=='BLOCK'``
        always returns 1.0. This implements the issue #101 "BLOCK
        shard 항상 top surface" hard rail — relevance cannot dim a
        critical preflight signal.
        """

        if (
            shard.kind == ShardKind.MISTAKE
            and (shard.blocker_level or "").upper() == "BLOCK"
        ):
            return 1.0

        components = {
            "recency": self._recency_score(shard, request_context),
            "topic_overlap": self._topic_overlap_score(shard, request_context),
            "role_match": self._role_match_score(shard, request_context),
            "source_trust": self._source_trust_score(shard),
        }
        total = 0.0
        for key, weight in self.weights.items():
            total += float(weight) * float(components.get(key, 0.0))
        return max(0.0, min(1.0, total))

    # ------------------------------------------------------------------
    # Component scores (exposed for tests + harness debugging)
    # ------------------------------------------------------------------

    def _recency_score(
        self,
        shard: MemoryShard,
        request_context: RequestContext,
    ) -> float:
        created = _parse_iso(shard.created_at)
        if created is None:
            return 0.0
        now = request_context.now or datetime.now(tz=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age_days = (now - created).total_seconds() / 86_400.0
        if age_days <= 0:
            return 1.0
        horizon = max(1, int(self.horizon_days or 1))
        if age_days >= horizon:
            return 0.0
        return max(0.0, 1.0 - (age_days / horizon))

    def _topic_overlap_score(
        self,
        shard: MemoryShard,
        request_context: RequestContext,
    ) -> float:
        request_tags = request_context.normalised_tags()
        if not request_tags:
            return 0.0
        shard_tags = frozenset(t.strip().lower() for t in shard.topic_tags if t)
        if not shard_tags:
            return 0.0
        intersection = shard_tags & request_tags
        union = shard_tags | request_tags
        if not union:
            return 0.0
        return len(intersection) / len(union)

    def _role_match_score(
        self,
        shard: MemoryShard,
        request_context: RequestContext,
    ) -> float:
        role = (request_context.role or "").strip().lower()
        if not role:
            return 0.0
        source = (shard.source or "").lower()
        if not source:
            return 0.0
        # Role names are typically slashed (e.g. "engineering-agent/ai-engineer").
        # Match against either the full string or the trailing segment.
        if role in source:
            return 1.0
        last_segment = role.rsplit("/", 1)[-1]
        if last_segment and last_segment in source:
            return 1.0
        return 0.0

    def _source_trust_score(self, shard: MemoryShard) -> float:
        return float(SOURCE_TRUST.get(shard.kind, 0.0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # Accept trailing "Z" as UTC
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = (
    "DEFAULT_WEIGHTS",
    "RelevanceSelector",
)
