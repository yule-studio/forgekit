"""Request-time knowledge retrieval — pick top-k items for a request.

Master plan §6.2 (request-time retrieval) needs the discussion layer
to surface "what does the company already know about this topic /
role / task_type?" without dumping every collected item into the
prompt. This module owns that selection step.

Two surfaces:

  1. :class:`KnowledgeRecord` — lightweight projection of a stored
     :class:`EngineeringKnowledgeItem`. Carries only what the
     synthesizer needs (title / source url / role / axes / rag tags /
     summary / freshness / importance / source name + topic key).
     Built either from an :class:`EngineeringKnowledgeItem`
     (``KnowledgeRecord.from_item``) or directly from a vault index
     row.
  2. :class:`KnowledgeRetriever` — callable that takes a candidate
     iterable + (query / role / task_type / axis_hints) and returns
     the top-k records by score. Score combines:

       * role match (+3 if the record's role equals the requested
         role, +1 if it's covered as a "secondary" role tag).
       * axis hint match (+2 per axis the record covers from
         ``axis_hints_for_task_type(task_type)``).
       * topic / rag-tag overlap (+1 per overlap, capped at +3).
       * importance bonus (critical=+2, high=+1).
       * freshness bonus (collected within the last 7 days = +1,
         within 30 days = +0.5).
       * empty summary penalty (−1).

The score numbers are deterministic and additive so tests can pin
exact ordering. The synthesizer can use the same
:class:`KnowledgeMatch` envelope to surface "why this knowledge item
was selected" alongside the body.

Strict no-I/O. Vault lookup is the caller's job — they hand in
candidates via the ``knowledge_loader`` seam on
:class:`ContextPackBuilder`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import (
    EngineeringKnowledgeItem,
    Importance,
    KnowledgeShareScope,
    SourceAxis,
)
from .source_registry import axis_hints_for_task_type


# ---------------------------------------------------------------------------
# Lightweight projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeRecord:
    """Per-item info the discussion layer cares about.

    Designed to be cheap to construct from either an in-memory
    :class:`EngineeringKnowledgeItem` or a vault index row. All fields
    have defaults so partial rows still flow through scoring (they
    just earn fewer points).
    """

    topic_key: str
    title: str
    role: str
    source_url: str = ""
    source_name: str = ""
    summary: str = ""
    axes: Tuple[SourceAxis, ...] = ()
    rag_tags: Tuple[str, ...] = ()
    importance: Importance = Importance.MEDIUM
    collected_at: str = ""  # ISO-8601
    note_path: Optional[str] = None
    project: Optional[str] = None
    secondary_roles: Tuple[str, ...] = ()
    share_scope: KnowledgeShareScope = KnowledgeShareScope.PUBLIC
    share_scope_reason: str = ""

    @classmethod
    def from_item(
        cls,
        item: EngineeringKnowledgeItem,
        *,
        axes: Sequence[SourceAxis] = (),
        secondary_roles: Sequence[str] = (),
        note_path: Optional[str] = None,
    ) -> "KnowledgeRecord":
        """Build from a stored :class:`EngineeringKnowledgeItem`.

        ``axes`` comes from the source registry — caller looks up the
        item's source by name and copies its axis tuple in. We don't
        store axes on the item itself because an item is bound to a
        source's *knowledge*, not the source's transport metadata.
        """

        return cls(
            topic_key=item.topic_key,
            title=item.title,
            role=item.role,
            source_url=item.source_url,
            source_name=item.source_name,
            summary=item.summary,
            axes=tuple(axes),
            rag_tags=tuple(item.rag_tags),
            importance=item.importance,
            collected_at=item.collected_at,
            note_path=note_path,
            secondary_roles=tuple(secondary_roles),
            share_scope=item.share_scope,
            share_scope_reason=item.share_scope_reason,
        )

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "topic_key": self.topic_key,
            "title": self.title,
            "role": self.role,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "summary": self.summary,
            "axes": [axis.value for axis in self.axes],
            "rag_tags": list(self.rag_tags),
            "importance": self.importance.value,
            "collected_at": self.collected_at,
            "note_path": self.note_path,
            "project": self.project,
            "secondary_roles": list(self.secondary_roles),
            "share_scope": self.share_scope.value,
            "share_scope_reason": self.share_scope_reason,
        }


@dataclass(frozen=True)
class KnowledgeMatch:
    """Scored row — record + score + signals.

    Used for tests and for surfacing "why this knowledge item was
    picked" on the operator dashboard. The synthesizer can drop the
    score / signals if it only needs the record.
    """

    record: KnowledgeRecord
    score: float
    signals: Tuple[str, ...]

    def evidence_labels(self) -> Tuple[str, ...]:
        """Return the score signals as human-readable Korean labels.

        The raw signals are short tokens optimised for tests
        (``role_primary_match``, ``axis_overlap:security,...``,
        ``importance_critical``, ``fresh_7d``). For Obsidian /
        Discord surfaces we want labels a human can read. This is a
        deterministic projection — no I/O, no formatting beyond
        ``label_for_signal``.
        """

        return tuple(label_for_signal(sig) for sig in self.signals if sig)


def label_for_signal(signal: str) -> str:
    """Map a retrieval signal token to a Korean label.

    Unknown signals fall through with a leading "기타: " prefix so the
    operator can still see the raw token without mistaking it for a
    typo. Axis overlap signals carry a comma-separated list of axes;
    we keep the axis names verbatim because they're structured tokens
    the operator already understands.
    """

    if not signal:
        return ""
    if signal.startswith("axis_overlap:"):
        axes = signal.split(":", 1)[1]
        return f"task_type 축 일치 ({axes})"
    if signal.startswith("topic_overlap:"):
        count = signal.split(":", 1)[1]
        return f"질문 토큰 겹침 (+{count})"
    return _KNOWN_SIGNAL_LABELS.get(signal, f"기타: {signal}")


_KNOWN_SIGNAL_LABELS: Mapping[str, str] = {
    "role_primary_match": "요청 역할과 정확히 일치",
    "role_secondary_match": "요청 역할이 보조 역할로 등록됨",
    "importance_critical": "중요도 critical",
    "importance_high": "중요도 high",
    "importance_low": "중요도 low (감점)",
    "fresh_7d": "최근 7일 이내 수집",
    "fresh_30d": "최근 30일 이내 수집",
    "empty_body_penalty": "본문/태그 비어 있음 (감점)",
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_TOKEN_PATTERN = re.compile(r"[\w가-힣]+", re.UNICODE)


def _tokens(text: Optional[str]) -> set[str]:
    if not text:
        return set()
    return {tok.lower() for tok in _TOKEN_PATTERN.findall(text) if len(tok) >= 2}


def _normalize_role(role: Optional[str]) -> str:
    if not role:
        return ""
    return role.strip().lower().split("/")[-1]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_bonus(
    collected_at: Optional[str], *, now: Optional[datetime] = None
) -> Tuple[float, str]:
    parsed = _parse_iso(collected_at)
    if parsed is None:
        return (0.0, "")
    anchor = now or datetime.now(tz=timezone.utc)
    age = anchor - parsed
    if age <= timedelta(days=7):
        return (1.0, "fresh_7d")
    if age <= timedelta(days=30):
        return (0.5, "fresh_30d")
    return (0.0, "")


_IMPORTANCE_BONUS: Mapping[Importance, Tuple[float, str]] = {
    Importance.CRITICAL: (2.0, "importance_critical"),
    Importance.HIGH: (1.0, "importance_high"),
    Importance.MEDIUM: (0.0, ""),
    Importance.LOW: (-0.5, "importance_low"),
}


def score_knowledge_record(
    record: KnowledgeRecord,
    *,
    query: Optional[str] = None,
    role: Optional[str] = None,
    task_type: Optional[str] = None,
    axis_hints: Sequence[SourceAxis] = (),
    now: Optional[datetime] = None,
) -> KnowledgeMatch:
    """Deterministic relevance score for a single record.

    *axis_hints* overrides ``axis_hints_for_task_type(task_type)`` so
    the caller can supply a custom hint set (useful when the request
    explicitly mentions an axis like "security").
    """

    score = 0.0
    signals: List[str] = []

    # Role match
    requested_role = _normalize_role(role)
    record_role = _normalize_role(record.role)
    if requested_role and record_role:
        if requested_role == record_role:
            score += 3.0
            signals.append("role_primary_match")
        elif requested_role in {_normalize_role(r) for r in record.secondary_roles}:
            score += 1.0
            signals.append("role_secondary_match")

    # Axis hint match
    if not axis_hints and task_type:
        axis_hints = axis_hints_for_task_type(task_type)
    if axis_hints:
        record_axes = set(record.axes)
        overlap = record_axes & set(axis_hints)
        if overlap:
            score += 2.0 * len(overlap)
            signals.append(
                "axis_overlap:" + ",".join(sorted(a.value for a in overlap))
            )

    # Topic / rag tag overlap
    query_tokens = _tokens(query)
    if query_tokens:
        haystack_tokens = (
            _tokens(record.title)
            | _tokens(record.summary)
            | _tokens(record.topic_key)
            | {t.lower() for t in record.rag_tags if t}
        )
        overlap = query_tokens & haystack_tokens
        if overlap:
            bonus = min(len(overlap), 3)
            score += float(bonus)
            signals.append(f"topic_overlap:{bonus}")

    # Importance bonus
    importance_bonus, importance_signal = _IMPORTANCE_BONUS.get(
        record.importance, (0.0, "")
    )
    if importance_bonus:
        score += importance_bonus
        if importance_signal:
            signals.append(importance_signal)

    # Freshness bonus
    fresh_bonus, fresh_signal = _freshness_bonus(record.collected_at, now=now)
    if fresh_bonus:
        score += fresh_bonus
        if fresh_signal:
            signals.append(fresh_signal)

    # Empty body penalty (signal-light record)
    if not record.summary and not record.rag_tags:
        score -= 1.0
        signals.append("empty_body_penalty")

    return KnowledgeMatch(record=record, score=score, signals=tuple(signals))


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeRetriever:
    """Score + truncate candidate :class:`KnowledgeRecord` rows.

    Callable: ``retriever(candidates=..., query=..., role=...,
    task_type=..., limit=5)``. Returns a tuple of
    :class:`KnowledgeRecord` (the synthesizer rarely needs the score).
    Use :meth:`with_signals` when the caller wants the score signal
    payload.
    """

    min_score: float = 1.0
    now: Optional[datetime] = None

    def __call__(
        self,
        *,
        candidates: Iterable[Any],
        query: Optional[str] = None,
        role: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 5,
    ) -> Tuple[KnowledgeRecord, ...]:
        matches = self.with_signals(
            candidates=candidates,
            query=query,
            role=role,
            task_type=task_type,
            limit=limit,
        )
        return tuple(match.record for match in matches)

    def with_signals(
        self,
        *,
        candidates: Iterable[Any],
        query: Optional[str] = None,
        role: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 5,
    ) -> Tuple[KnowledgeMatch, ...]:
        scored: List[Tuple[float, int, KnowledgeMatch]] = []
        for idx, candidate in enumerate(candidates):
            record = _coerce_record(candidate)
            if record is None:
                continue
            match = score_knowledge_record(
                record,
                query=query,
                role=role,
                task_type=task_type,
                now=self.now,
            )
            if match.score < self.min_score:
                continue
            # Sort: score desc, freshness desc, idx asc.
            freshness_key = _freshness_sort_key(record.collected_at)
            scored.append((match.score, idx, match))
            # ``freshness_key`` is folded into the secondary key by the
            # final sort below to keep ties deterministic.
        scored.sort(
            key=lambda triplet: (
                -triplet[0],
                _freshness_sort_key(triplet[2].record.collected_at),
                triplet[1],
            )
        )
        return tuple(match for _, _, match in scored[: max(limit, 0)])


def _freshness_sort_key(collected_at: str) -> str:
    """Return an ASCII key that orders newer-first when ascending."""

    if not collected_at:
        return ""
    return "".join(
        chr(255 - ord(c)) if ord(c) < 255 else c for c in collected_at
    )


def _coerce_record(candidate: Any) -> Optional[KnowledgeRecord]:
    """Accept :class:`KnowledgeRecord` / ``EngineeringKnowledgeItem`` /
    plain Mapping; coerce or return None.

    Mappings need at least ``topic_key``, ``title``, ``role`` to count.
    """

    if isinstance(candidate, KnowledgeRecord):
        return candidate
    if isinstance(candidate, EngineeringKnowledgeItem):
        return KnowledgeRecord.from_item(candidate)
    if isinstance(candidate, Mapping):
        topic_key = str(candidate.get("topic_key") or "").strip()
        title = str(candidate.get("title") or "").strip()
        role = str(candidate.get("role") or "").strip()
        if not (topic_key and title and role):
            return None
        axes_raw = candidate.get("axes") or ()
        axes: List[SourceAxis] = []
        for ax in axes_raw:
            try:
                axes.append(SourceAxis(ax))
            except ValueError:
                continue
        importance_raw = candidate.get("importance") or "medium"
        try:
            importance = Importance(importance_raw)
        except ValueError:
            importance = Importance.MEDIUM
        share_raw = candidate.get("share_scope") or "public"
        try:
            share_scope = KnowledgeShareScope(share_raw)
        except ValueError:
            share_scope = KnowledgeShareScope.PUBLIC
        return KnowledgeRecord(
            topic_key=topic_key,
            title=title,
            role=role,
            source_url=str(candidate.get("source_url") or ""),
            source_name=str(candidate.get("source_name") or ""),
            summary=str(candidate.get("summary") or ""),
            axes=tuple(axes),
            rag_tags=tuple(candidate.get("rag_tags") or ()),
            importance=importance,
            collected_at=str(candidate.get("collected_at") or ""),
            note_path=candidate.get("note_path"),
            project=candidate.get("project"),
            secondary_roles=tuple(candidate.get("secondary_roles") or ()),
            share_scope=share_scope,
            share_scope_reason=str(candidate.get("share_scope_reason") or ""),
        )
    return None


__all__ = [
    "KnowledgeMatch",
    "KnowledgeRecord",
    "KnowledgeRetriever",
    "label_for_signal",
    "score_knowledge_record",
]
