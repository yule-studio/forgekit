"""Role-aware retrieval helpers built on the memory search layer.

The deliberation/team-runtime modules call :func:`fetch_role_context`
right before a role takes its turn (or before tech-lead synthesizes) to
pull the most relevant past notes/policies/workflow artifacts. Failures
return an empty list rather than raising, so retrieval can never break
the deterministic fallback path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Opt-in: re-rank fetched hits by the memory-policy section 4 reuse boost
# (canonical / reusable / decision / retrospective) before returning. Default
# off keeps the existing slot-priority order byte-for-byte.
ENV_RETRIEVAL_BOOST = "YULE_RETRIEVAL_BOOST_ENABLED"
# Opt-in: cache the fetched candidate pool keyed by (role, query, …) and the
# index file mtime. Invalidates automatically on reindex (mtime changes).
ENV_RETRIEVAL_CACHE = "YULE_RETRIEVAL_CACHE_ENABLED"

# (cache_key) -> (index_mtime, results). Module-level; bounded by query variety.
_POOL_CACHE: Dict[Tuple, Tuple[Optional[float], List]] = {}


def _retrieval_boost_enabled() -> bool:
    return (os.environ.get(ENV_RETRIEVAL_BOOST) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _retrieval_cache_enabled() -> bool:
    return (os.environ.get(ENV_RETRIEVAL_CACHE) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _index_mtime(repo_root: Optional[Path]) -> Optional[float]:
    """Index db mtime for cache invalidation; None when unresolvable (→ no cache)."""

    try:
        from yule_memory.indexer import _resolve_db_path

        db = _resolve_db_path(db_path=None, repo_root=repo_root)
        return db.stat().st_mtime if db and db.exists() else None
    except Exception:  # noqa: BLE001 - cache is best-effort
        return None


def clear_retrieval_cache() -> None:
    """Drop the candidate-pool cache (tests / explicit invalidation)."""

    _POOL_CACHE.clear()

from ..agents.deliberation import RetrievedMemory, assign_citation_ids
from yule_memory.models import (
    NOTE_KIND_DECISION,
    NOTE_KIND_REFERENCE,
    NOTE_KIND_RESEARCH,
    SOURCE_OBSIDIAN,
    SOURCE_POLICY,
    SOURCE_WORKFLOW,
)
from yule_memory.search import search


_log = logging.getLogger(__name__)


# Per-role source priority. Earlier entries are queried first; results
# are merged in priority order so each slot's first hit dominates the
# top of the merged list. Roles not in the table fall back to "decision
# → policy → research → reference" which is the gateway/tech-lead view.
_ROLE_SOURCE_PRIORITY: dict[str, Sequence[tuple[Optional[str], Optional[str]]]] = {
    "tech-lead": (
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_POLICY, None),
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
        (SOURCE_WORKFLOW, None),
    ),
    "ai-engineer": (
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_POLICY, None),
    ),
    "product-designer": (
        (SOURCE_OBSIDIAN, NOTE_KIND_REFERENCE),
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
    ),
    "backend-engineer": (
        (SOURCE_POLICY, None),
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
    ),
    "frontend-engineer": (
        (SOURCE_OBSIDIAN, NOTE_KIND_REFERENCE),
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_POLICY, None),
    ),
    "qa-engineer": (
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_POLICY, None),
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
    ),
    "devops-engineer": (
        (SOURCE_POLICY, None),
        (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
        (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
        (SOURCE_WORKFLOW, None),
    ),
}

_DEFAULT_PRIORITY: Sequence[tuple[Optional[str], Optional[str]]] = (
    (SOURCE_OBSIDIAN, NOTE_KIND_DECISION),
    (SOURCE_POLICY, None),
    (SOURCE_OBSIDIAN, NOTE_KIND_RESEARCH),
    (SOURCE_OBSIDIAN, NOTE_KIND_REFERENCE),
)


def fetch_role_context(
    *,
    role: str,
    query: str,
    task_type: Optional[str] = None,
    limit: int = 3,
    repo_root: Optional[Path] = None,
) -> List[RetrievedMemory]:
    """Return up to ``limit`` retrieved memories for ``role`` and ``query``.

    The function never raises: any indexer/search failure is logged at
    ``warning`` and an empty list is returned, so deliberation continues
    on its deterministic path.
    """

    if not query or not query.strip():
        return []
    short = _short_role(role)
    priority = _ROLE_SOURCE_PRIORITY.get(short, _DEFAULT_PRIORITY)

    boost_enabled = _retrieval_boost_enabled()

    # Candidate-pool cache (opt-in): serve a memoized result while the index is
    # unchanged. Invalidates automatically when the index mtime moves (reindex).
    cache_on = _retrieval_cache_enabled()
    cache_key: Optional[Tuple] = None
    cache_mtime: Optional[float] = None
    if cache_on:
        cache_key = (short, query.strip(), task_type, limit, boost_enabled, str(repo_root or ""))
        cache_mtime = _index_mtime(repo_root)
        if cache_mtime is not None:
            cached = _POOL_CACHE.get(cache_key)
            if cached is not None and cached[0] == cache_mtime:
                return list(cached[1])
    seen_ids: set[str] = set()
    merged: List[RetrievedMemory] = []
    raw_pool: List[object] = []  # MemorySearchResult, for the boost re-rank
    # When boosting, collect a wider candidate pool so a high-boost hit from a
    # lower-priority slot can still surface into the top *limit*.
    pool_target = max(limit * 3, limit) if boost_enabled else limit
    per_slot = max(1, limit)

    for source_kind, note_kind in priority:
        if len(merged) >= pool_target:
            break
        try:
            hits = search(
                query,
                limit=per_slot,
                source_kind=source_kind,
                note_kind=note_kind,
                task_type=task_type,
                repo_root=repo_root,
            )
        except Exception as exc:  # noqa: BLE001 - retrieval is best-effort
            _log.warning("memory retrieval failed (role=%s): %s", role, exc)
            continue
        for hit in hits:
            doc_id = hit.document.doc_id
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            raw_pool.append(hit)
            merged.append(_to_retrieved_memory(hit))
            if len(merged) >= pool_target:
                break

    if boost_enabled and raw_pool:
        ordered = _apply_boost(raw_pool, limit=limit)
    else:
        ordered = merged[:limit]
    # Stamp citation IDs at the boundary so callers (deterministic
    # fallbacks + future LLM runners) get the same labels regardless of
    # which entry point produced the list.
    result = list(assign_citation_ids(tuple(ordered)))
    if cache_on and cache_key is not None and cache_mtime is not None:
        _POOL_CACHE[cache_key] = (cache_mtime, list(result))
    return result


def _apply_boost(raw_pool: Sequence[object], *, limit: int) -> List[RetrievedMemory]:
    """Re-rank raw hits by the reuse boost and return the top *limit*.

    Never raises: any failure falls back to the unboosted slot order so
    retrieval stays best-effort.
    """

    try:
        from ..agents.harness.retrieval_boost import rerank
    except Exception:  # noqa: BLE001
        return [_to_retrieved_memory(h) for h in raw_pool[:limit]]
    try:
        boosted = rerank(raw_pool)[:limit]
    except Exception as exc:  # noqa: BLE001
        _log.warning("retrieval boost re-rank failed: %s", exc)
        return [_to_retrieved_memory(h) for h in raw_pool[:limit]]
    by_path = {h.document.path: h for h in raw_pool}
    out: List[RetrievedMemory] = []
    for b in boosted:
        hit = by_path.get(b.path)
        if hit is not None:
            out.append(_to_retrieved_memory(hit))
    return out


def fetch_topic_context(
    *,
    topic: str,
    query: Optional[str] = None,
    limit: int = 5,
    repo_root: Optional[Path] = None,
) -> List[RetrievedMemory]:
    """Topic-横단 recall (recall-policy section 4): notes sharing a ``topic``.

    Matches the Obsidian frontmatter ``topic`` key (projected into the index
    ``extra``). Searches broadly on *query* (defaults to *topic*) then keeps only
    hits whose ``extra['topic']`` equals *topic*. Never raises — returns ``[]``
    on any failure so it can't break a deliberation path.
    """

    topic_norm = (topic or "").strip()
    if not topic_norm:
        return []
    q = (query or topic_norm).strip()
    try:
        hits = search(q, limit=max(limit * 5, limit), repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001 - topic recall is best-effort
        _log.warning("topic recall search failed (topic=%s): %s", topic_norm, exc)
        return []
    matched = [
        h for h in hits if str((getattr(h.document, "extra", {}) or {}).get("topic", "")) == topic_norm
    ][:limit]
    return list(assign_citation_ids(tuple(_to_retrieved_memory(h) for h in matched)))


def _to_retrieved_memory(hit) -> RetrievedMemory:
    doc = hit.document
    return RetrievedMemory(
        title=doc.title or "",
        snippet=hit.snippet or "",
        source_kind=doc.source_kind,
        role=doc.role,
        note_kind=doc.note_kind,
        path=doc.path,
        score=float(hit.score),
    )


def _short_role(role: str) -> str:
    """Normalize ``engineering-agent/tech-lead`` → ``tech-lead``."""

    cleaned = (role or "").strip()
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[-1]
    return cleaned.strip()
