"""Claude Mem unifier — F10 / issue #101.

Cross-session 장기 메모리 정형화 계층. 다음 5개 source 를 단일
``MemorySource`` Protocol 뒤에 묶어 "이전 세션 / 결정 / 실수 / 회의록"
이 다음 작업 시작 시점에 자동 surface 되게 한다:

  * **Obsidian vault-mirror** — ``notes/vault-mirror/`` 의 노트
  * **session.extra** — round-1 short-term ledger
  * **mistake_ledger** — F2 의 cross-session 영구 mistake DB
  * **decision** — agent_ops_audit 의 ``DECISION`` action
  * **audit** — agent_ops_audit 의 일반 운영 이벤트

각 source 는 :class:`MemoryShard` 시퀀스를 read-only 로 노출하며,
:class:`LongTermMemory` 가 query (topic / role / issue) 단위로 fanout
한 다음 :class:`RelevanceSelector` 가 deterministic ranking 한다.

Hard rails (governance regression-tested):

  * 모든 source 는 read-only — :meth:`MemorySource.query` 는 결과를
    반환만 하고 원본 데이터를 mutate 하지 않는다.
  * env ``YULE_LONG_TERM_MEMORY_ENABLED=false`` → 회로 단절. 빈
    :class:`MemoryPack` 을 즉시 반환해 caller 흐름을 깨지 않는다.
  * mistake BLOCK shard 는 항상 ``MemoryPack.shards[0]`` 에 surface.
    relevance score 와 무관하게 검열하지 않는다 (검열은 caller 의
    PasteGuard wrapping 책임).
  * PasteGuard 통합은 fail-closed — shard ``content`` 의 outbound
    masking 은 caller 가 :func:`guard_outbound` 로 감싸야 한다.
"""

from __future__ import annotations

import enum
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Env knobs
# ---------------------------------------------------------------------------


ENV_LONG_TERM_MEMORY_ENABLED: str = "YULE_LONG_TERM_MEMORY_ENABLED"
ENV_MEMORY_FRESHNESS_DAYS: str = "YULE_MEMORY_FRESHNESS_DAYS"
ENV_MEMORY_MAX_SHARDS_PER_QUERY: str = "YULE_MEMORY_MAX_SHARDS_PER_QUERY"

DEFAULT_FRESHNESS_DAYS: int = 30
DEFAULT_MAX_SHARDS_PER_QUERY: int = 10


def long_term_memory_enabled() -> bool:
    """Return whether the unified long-term memory layer is on.

    Defaults to **False** so the layer is opt-in. Operator flips the
    env to ``true`` (case-insensitive) to wire it into the runtime.
    """

    value = os.environ.get(ENV_LONG_TERM_MEMORY_ENABLED, "false")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def freshness_days() -> int:
    """Return the recency window (days) used by :class:`RelevanceSelector`."""

    raw = os.environ.get(ENV_MEMORY_FRESHNESS_DAYS, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_FRESHNESS_DAYS
    if value <= 0:
        return DEFAULT_FRESHNESS_DAYS
    return value


def max_shards_per_query() -> int:
    """Return the per-source fanout cap."""

    raw = os.environ.get(ENV_MEMORY_MAX_SHARDS_PER_QUERY, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_SHARDS_PER_QUERY
    if value <= 0:
        return DEFAULT_MAX_SHARDS_PER_QUERY
    return value


# ---------------------------------------------------------------------------
# Shard kinds + source trust
# ---------------------------------------------------------------------------


class ShardKind(str, enum.Enum):
    """Enumerated kinds of memory shards.

    Stored as a string enum so payloads can quote the literal kind
    value without leaking implementation detail. Source-trust scores
    in :class:`RelevanceSelector` are indexed by this enum.
    """

    OBSIDIAN_NOTE = "obsidian_note"
    SESSION_EXTRA = "session_extra"
    MISTAKE = "mistake"
    DECISION = "decision"
    AUDIT = "audit"


# Source trust weight (0..1) used as one of the 4 RelevanceSelector
# components. BLOCK mistake shards are special-cased to 1.0 by
# :meth:`RelevanceSelector.score` so the preflight signal always wins.
SOURCE_TRUST: Mapping[ShardKind, float] = {
    ShardKind.DECISION: 0.9,
    ShardKind.MISTAKE: 0.85,
    ShardKind.AUDIT: 0.8,
    ShardKind.OBSIDIAN_NOTE: 0.7,
    ShardKind.SESSION_EXTRA: 0.5,
}


def _coerce_kind(value: Any) -> ShardKind:
    """Coerce *value* into a :class:`ShardKind` (defaults to AUDIT)."""

    if isinstance(value, ShardKind):
        return value
    text = str(value or "").strip().lower()
    for member in ShardKind:
        if member.value == text:
            return member
    return ShardKind.AUDIT


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryShard:
    """A single addressable unit of long-term memory.

    The shape is intentionally narrow so heterogeneous sources can
    project into one common surface. ``content`` is plain-text only —
    binary or structured blobs must be summarised by the adapter
    before being lifted into a shard.

    Hard rails:

      * ``hash`` is a sha256 over (kind, source, content) — caller can
        de-dupe two shards from different sources but with identical
        content. The hash also lets audit trails reference a shard
        without re-shipping the content.
      * ``created_at`` is ISO8601 with explicit timezone (UTC by
        default). :meth:`RelevanceSelector._recency_score` parses it.
      * ``topic_tags`` is a tuple of lower-case tokens; the TopicIndex
        builds an inverse map from these.
      * ``related_issue`` / ``related_pr`` are optional ints; when set
        :meth:`LongTermMemory.for_issue` filters by them.
    """

    kind: ShardKind
    source: str
    content: str
    created_at: str
    topic_tags: Tuple[str, ...] = ()
    related_issue: Optional[int] = None
    related_pr: Optional[int] = None
    hash: str = ""
    blocker_level: Optional[str] = None  # set by MistakeLedgerSource

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ShardKind):
            object.__setattr__(self, "kind", _coerce_kind(self.kind))
        if not self.hash:
            object.__setattr__(self, "hash", _shard_hash(
                kind=self.kind, source=self.source, content=self.content
            ))
        if self.topic_tags and not isinstance(self.topic_tags, tuple):
            object.__setattr__(self, "topic_tags", tuple(self.topic_tags))

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "content": self.content,
            "created_at": self.created_at,
            "topic_tags": list(self.topic_tags),
            "related_issue": self.related_issue,
            "related_pr": self.related_pr,
            "hash": self.hash,
            "blocker_level": self.blocker_level,
        }


@dataclass(frozen=True)
class MemoryFilter:
    """Query filter forwarded to :class:`MemorySource` adapters.

    Adapters use whichever fields make sense — e.g. ObsidianVault
    matches on ``topic_tags`` / ``role``, MistakeLedger on ``role``,
    Decision/Audit on ``issue``. Unknown filter fields are ignored.
    """

    role: Optional[str] = None
    topic_tags: Tuple[str, ...] = ()
    issue: Optional[int] = None
    pr: Optional[int] = None
    since: Optional[str] = None  # ISO8601 lower bound
    limit: int = DEFAULT_MAX_SHARDS_PER_QUERY


@dataclass(frozen=True)
class MemoryPack:
    """Ranked, deduplicated bundle of shards returned to callers.

    Empty (``shards=()``) when the long-term memory layer is disabled
    or no source produced a relevant shard. ``query_signature`` is a
    stable token of the originating request — two identical requests
    yield equal signatures so audit logs can correlate.
    """

    shards: Tuple[MemoryShard, ...]
    generated_at: str
    query_signature: str

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "shards": [s.to_payload() for s in self.shards],
            "generated_at": self.generated_at,
            "query_signature": self.query_signature,
        }

    @property
    def is_empty(self) -> bool:
        return not self.shards


@dataclass(frozen=True)
class RequestContext:
    """Lightweight context bundle the relevance selector consumes.

    Pure data — no I/O. Caller hydrates it from whatever the worker
    already knows (active role, request topic tags, optional issue
    number). Unknown fields default to empty so older callers do not
    break when new signals are added.
    """

    role: Optional[str] = None
    topic_tags: Tuple[str, ...] = ()
    issue: Optional[int] = None
    pr: Optional[int] = None
    now: Optional[datetime] = None

    def normalised_tags(self) -> frozenset[str]:
        return frozenset(_normalise_token(t) for t in self.topic_tags if t)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MemorySource(Protocol):
    """Read-only adapter that yields shards for a filter.

    Implementations **must not** mutate their backing store. The
    governance regression test introspects each adapter for ``write``
    / ``insert`` / ``delete`` methods and fails closed if found.
    """

    kind: ShardKind

    def query(self, filter: MemoryFilter) -> Iterable[MemoryShard]:  # pragma: no cover - Protocol
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalise_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text


def tokenize(value: str) -> frozenset[str]:
    """Lower-case ASCII tokenisation used by topic / role matching."""

    text = str(value or "").strip().lower()
    if not text:
        return frozenset()
    return frozenset(_TOKEN_RE.findall(text))


def _shard_hash(*, kind: ShardKind, source: str, content: str) -> str:
    digest = hashlib.sha256(
        f"{kind.value}|{source}|{content}".encode("utf-8", errors="replace")
    ).hexdigest()
    return f"sha256:{digest[:24]}"


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _query_signature(*, request_context: RequestContext, limit: int) -> str:
    parts = [
        request_context.role or "",
        ",".join(sorted(_normalise_token(t) for t in request_context.topic_tags)),
        str(request_context.issue or ""),
        str(request_context.pr or ""),
        str(limit),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"q-{digest[:16]}"


# Re-export adapter modules at package level so callers do
# `from yule_engineering.agents.memory import LongTermMemory` etc.
from .long_term_memory import (  # noqa: E402
    LongTermMemory,
    build_memory_pack,
)
from .relevance_selector import RelevanceSelector  # noqa: E402
from .topic_index import TopicIndex  # noqa: E402
from .sources import (  # noqa: E402
    AuditSource,
    DecisionSource,
    MistakeLedgerSource,
    ObsidianVaultSource,
    SessionExtraSource,
)


__all__ = (
    "AuditSource",
    "DEFAULT_FRESHNESS_DAYS",
    "DEFAULT_MAX_SHARDS_PER_QUERY",
    "DecisionSource",
    "ENV_LONG_TERM_MEMORY_ENABLED",
    "ENV_MEMORY_FRESHNESS_DAYS",
    "ENV_MEMORY_MAX_SHARDS_PER_QUERY",
    "LongTermMemory",
    "MemoryFilter",
    "MemoryPack",
    "MemoryShard",
    "MemorySource",
    "MistakeLedgerSource",
    "ObsidianVaultSource",
    "RelevanceSelector",
    "RequestContext",
    "SOURCE_TRUST",
    "SessionExtraSource",
    "ShardKind",
    "TopicIndex",
    "build_memory_pack",
    "freshness_days",
    "long_term_memory_enabled",
    "max_shards_per_query",
    "tokenize",
)
