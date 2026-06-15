"""Retrieval reuse-boost — live wiring of memory-policy section 4 (read-side boost).

``yule_memory.search`` ranks purely by FTS5 bm25 (lower score = better). The
recall/memory policies say reuse-shaped artifacts should rank higher:

    kind=decision            → +1.0   (다음 작업의 가장 강한 input)
    status=decided/approval  → +0.5
    frontmatter.reusable     → +1.0   (운영자 표식)
    frontmatter.canonical    → +2.0   (영역의 1차 source)
    kind=retrospective       → +0.5

This module applies that boost as a re-rank over already-fetched results and
turns each hit into a *reference* (title + path + snippet + why_retrieved)
rather than a full body — fewer tokens carried, more relevant ordering. It is
pure and deterministic; markers are read from ``note_kind`` + ``tags`` +
``extra`` so it works whether the boost markers are projected into the index
``extra`` or carried as tags.

bm25 is "lower is better"; boost is subtracted from the base score so boosted
hits sort earlier. The applied boost + reasons are surfaced so the benchmark /
receipt can show *why* a hit was retrieved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence, Tuple

# memory-policy section 4 — the single source of the boost weights.
BOOST_DECISION = 1.0
BOOST_RETROSPECTIVE = 0.5
BOOST_STATUS = 0.5
BOOST_REUSABLE = 1.0
BOOST_CANONICAL = 2.0

_STATUS_BOOSTED = frozenset({"decided", "approval-pending", "approval_pending"})
_TRUTHY = frozenset({"1", "true", "yes", "on", "y"})


@dataclass(frozen=True)
class BoostedResult:
    title: str
    path: str
    source_kind: str
    note_kind: str
    snippet: str
    base_score: float
    boost_score: float
    effective_score: float
    why_retrieved: Tuple[str, ...]

    def to_reference(self) -> dict:
        """Token-lean reference (no full body) for runner-fed context."""

        return {
            "title": self.title,
            "path": self.path,
            "source_kind": self.source_kind,
            "note_kind": self.note_kind,
            "snippet": self.snippet,
            "boost_score": round(self.boost_score, 3),
            "why_retrieved": list(self.why_retrieved),
        }


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in _TRUTHY


def _markers(doc: Any) -> Tuple[str, Mapping[str, Any], Sequence[str]]:
    note_kind = str(getattr(doc, "note_kind", "") or "").strip().lower()
    extra = getattr(doc, "extra", None)
    extra = extra if isinstance(extra, Mapping) else {}
    tags = getattr(doc, "tags", None) or ()
    tags_l = [str(t).strip().lower() for t in tags]
    return note_kind, extra, tags_l


def boost_for(doc: Any) -> Tuple[float, Tuple[str, ...]]:
    """Return ``(boost, reasons)`` for one document per memory-policy section 4."""

    note_kind, extra, tags = _markers(doc)
    boost = 0.0
    reasons: List[str] = []

    if note_kind == "decision":
        boost += BOOST_DECISION
        reasons.append(f"kind=decision (+{BOOST_DECISION})")
    elif note_kind == "retrospective":
        boost += BOOST_RETROSPECTIVE
        reasons.append(f"kind=retrospective (+{BOOST_RETROSPECTIVE})")

    status = str(extra.get("status", "")).strip().lower()
    if status in _STATUS_BOOSTED:
        boost += BOOST_STATUS
        reasons.append(f"status={status} (+{BOOST_STATUS})")

    if _truthy(extra.get("reusable")) or "reusable" in tags:
        boost += BOOST_REUSABLE
        reasons.append(f"reusable (+{BOOST_REUSABLE})")
    if _truthy(extra.get("canonical")) or "canonical" in tags:
        boost += BOOST_CANONICAL
        reasons.append(f"canonical (+{BOOST_CANONICAL})")

    return boost, tuple(reasons)


def _doc_of(result: Any) -> Any:
    return getattr(result, "document", result)


def _base_score(result: Any) -> float:
    score = getattr(result, "score", None)
    if score is None:
        doc = _doc_of(result)
        score = getattr(doc, "score", 0.0)
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def rerank(results: Sequence[Any]) -> List[BoostedResult]:
    """Re-rank *results* by boosted relevance (lower effective score = better).

    Stable: ties fall back to the original bm25 order. Each result keeps its
    base score, applied boost, and the human reasons it was boosted.
    """

    boosted: List[Tuple[int, BoostedResult]] = []
    for idx, result in enumerate(results):
        doc = _doc_of(result)
        base = _base_score(result)
        boost, reasons = boost_for(doc)
        effective = base - boost  # bm25 lower is better → subtract boost
        boosted.append(
            (
                idx,
                BoostedResult(
                    title=str(getattr(doc, "title", "") or ""),
                    path=str(getattr(doc, "path", "") or ""),
                    source_kind=str(getattr(doc, "source_kind", "") or ""),
                    note_kind=str(getattr(doc, "note_kind", "") or ""),
                    snippet=str(getattr(result, "snippet", "") or getattr(doc, "snippet", "") or ""),
                    base_score=base,
                    boost_score=boost,
                    effective_score=effective,
                    why_retrieved=reasons,
                ),
            )
        )
    boosted.sort(key=lambda pair: (pair[1].effective_score, pair[0]))
    return [b for _idx, b in boosted]


def to_references(results: Sequence[Any], *, limit: int = 3) -> List[dict]:
    """Boost + re-rank + project to token-lean references (top *limit*)."""

    return [b.to_reference() for b in rerank(results)[:limit]]


__all__ = (
    "BOOST_DECISION",
    "BOOST_RETROSPECTIVE",
    "BOOST_STATUS",
    "BOOST_REUSABLE",
    "BOOST_CANONICAL",
    "BoostedResult",
    "boost_for",
    "rerank",
    "to_references",
)
