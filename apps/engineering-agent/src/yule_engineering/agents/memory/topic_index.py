"""Token-based inverse index for memory shards (F10 / #101).

Pure in-memory secondary index built on top of a shard sequence. The
index pre-tokenises every shard's ``topic_tags`` + ``content`` head
once, then offers O(|query tokens|) lookup. The :class:`LongTermMemory`
facade uses it to narrow source-fanout candidates before passing them
to :class:`RelevanceSelector`.

Hard rails:

  * **Read-only**: :meth:`TopicIndex.add` builds the index in-place
    but no method ever mutates the underlying shards.
  * **Deterministic**: query results are sorted by (shard.created_at
    desc, shard.hash asc) so two equal builds yield equal ordering.
  * **Bounded**: only the first ``content_token_budget`` tokens of
    each shard contribute to the index so very long content cannot
    dominate the inverse map.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, FrozenSet, Iterable, List, Sequence, Set, Tuple

from . import MemoryShard, _TOKEN_RE, tokenize


DEFAULT_CONTENT_TOKEN_BUDGET: int = 32


class TopicIndex:
    """Inverse map ``token -> set[shard_hash]``.

    Construct empty and :meth:`add` shards, or pass an iterable to
    :meth:`from_shards`. The index is intentionally simple — no
    weighting, no stemming, no stopword filter. Token Jaccard for
    relevance happens in :class:`RelevanceSelector`; this class only
    answers "which shards share at least one token with this query".
    """

    def __init__(self, *, content_token_budget: int = DEFAULT_CONTENT_TOKEN_BUDGET) -> None:
        self._inverse: Dict[str, Set[str]] = defaultdict(set)
        self._shards_by_hash: Dict[str, MemoryShard] = {}
        self._content_token_budget = max(0, int(content_token_budget))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_shards(
        cls,
        shards: Iterable[MemoryShard],
        *,
        content_token_budget: int = DEFAULT_CONTENT_TOKEN_BUDGET,
    ) -> "TopicIndex":
        index = cls(content_token_budget=content_token_budget)
        for shard in shards:
            index.add(shard)
        return index

    def add(self, shard: MemoryShard) -> None:
        """Index *shard* by its tokens.

        Idempotent: re-adding the same shard hash is a no-op.
        """

        if shard.hash in self._shards_by_hash:
            return
        self._shards_by_hash[shard.hash] = shard
        for token in self._shard_tokens(shard):
            self._inverse[token].add(shard.hash)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def lookup(self, tokens: Iterable[str]) -> Tuple[MemoryShard, ...]:
        """Return shards sharing at least one normalised token with *tokens*.

        Each input string is tokenised through :func:`tokenize` (lower
        case ASCII word split) so callers can pass either single
        tokens or multi-word topics like ``"paste-guard"`` and the
        index matches via the underlying word components.

        Empty token set → empty result. Ordering is deterministic:
        ``(created_at desc, hash asc)``.
        """

        expanded: set = set()
        for raw in tokens:
            if not raw:
                continue
            expanded |= set(tokenize(str(raw)))
        normalised = frozenset(t for t in expanded if t)
        if not normalised:
            return ()
        candidate_hashes: Set[str] = set()
        for token in normalised:
            candidate_hashes |= self._inverse.get(token, set())
        if not candidate_hashes:
            return ()
        shards = [self._shards_by_hash[h] for h in candidate_hashes]
        shards.sort(key=lambda s: (s.created_at, s.hash), reverse=False)
        shards.sort(key=lambda s: s.created_at, reverse=True)
        return tuple(shards)

    def tokens_for(self, shard_hash: str) -> FrozenSet[str]:
        """Diagnostic helper — list tokens recorded for a shard hash."""

        out: Set[str] = set()
        for token, hashes in self._inverse.items():
            if shard_hash in hashes:
                out.add(token)
        return frozenset(out)

    def __len__(self) -> int:
        return len(self._shards_by_hash)

    def __contains__(self, shard_hash: object) -> bool:
        return shard_hash in self._shards_by_hash

    @property
    def size(self) -> int:
        return len(self._shards_by_hash)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _shard_tokens(self, shard: MemoryShard) -> FrozenSet[str]:
        tokens: Set[str] = set()
        for tag in shard.topic_tags or ():
            tokens.update(tokenize(tag))
        if self._content_token_budget > 0 and shard.content:
            # Preserve order so the budget caps the *leading* head of
            # the content, not an arbitrary subset of the frozenset.
            ordered = _TOKEN_RE.findall(shard.content.lower())
            tokens.update(ordered[: self._content_token_budget])
        return frozenset(t for t in tokens if t)

    @staticmethod
    def _normalise(token: str) -> str:
        text = str(token or "").strip().lower()
        return text


__all__ = (
    "DEFAULT_CONTENT_TOKEN_BUDGET",
    "TopicIndex",
)
