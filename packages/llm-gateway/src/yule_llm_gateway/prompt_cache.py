"""Deterministic prompt-cache *metadata* layer.

This is NOT a real provider cache. It computes a stable ``cache_key`` from the
request shape (provider, model, prompt/messages, generation params) and records
hit/miss metadata so a caller can reason about cache behaviour and surface it in
audit trails. The actual cached payload, if any, lives behind a provider's own
prompt-cache feature — this module only gives the platform a single, consistent
key + bookkeeping seam.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import LLMRequest


def compute_cache_key(request: LLMRequest) -> str:
    """Return a deterministic hex cache key for *request*.

    The key is a SHA-256 over a canonical JSON projection of the cache-relevant
    fields. ``metadata`` is intentionally excluded — it carries call-site bk
    (task id, role, ...) that must NOT change the key for otherwise-identical
    prompts, so two callers issuing the same prompt share a cache key.
    """

    canonical = {
        "provider": request.provider,
        "model": request.model,
        "prompt": request.prompt,
        "messages": [m.to_dict() for m in request.messages],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheLookup:
    """Result of :meth:`PromptCache.lookup` — key + hit/miss metadata."""

    cache_key: str
    hit: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"cache_key": self.cache_key, "hit": self.hit}


@dataclass
class PromptCache:
    """Tracks cache keys and hit/miss metadata for prompts.

    The store maps ``cache_key -> True`` once a key has been seen. ``lookup``
    reports whether the key was previously recorded (hit) and remembers it for
    next time, accumulating per-call metadata in :attr:`records`.

    No prompt *content* or response payload is stored — only keys and outcomes —
    so this stays a metadata layer with a small, bounded footprint.
    """

    _seen: Dict[str, bool] = field(default_factory=dict)
    records: List[CacheLookup] = field(default_factory=list)
    hits: int = 0
    misses: int = 0

    def key_for(self, request: LLMRequest) -> str:
        return compute_cache_key(request)

    def lookup(self, request: LLMRequest, *, remember: bool = True) -> CacheLookup:
        """Compute the key and report hit/miss, recording metadata.

        When *remember* is True (default) an unseen key is registered so the next
        identical request is reported as a hit. Pass ``remember=False`` for a
        pure probe that does not mutate the cache state.
        """

        key = compute_cache_key(request)
        hit = self._seen.get(key, False)
        if hit:
            self.hits += 1
        else:
            self.misses += 1
            if remember:
                self._seen[key] = True
        record = CacheLookup(cache_key=key, hit=hit)
        self.records.append(record)
        return record

    def last(self) -> Optional[CacheLookup]:
        return self.records[-1] if self.records else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "keys": sorted(self._seen),
        }


__all__ = (
    "PromptCache",
    "CacheLookup",
    "compute_cache_key",
)
