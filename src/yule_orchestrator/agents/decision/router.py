"""Decision router — Phase 3 of #73.

Two-stage routing:

  1. Deterministic fast-path — Korean / English keyword matching.
     Free for routine messages; covers ~80%+ of intake based on
     `role_profiles.activation_keywords` overlap analysis.
  2. Classifier (Protocol) — production wires this to a real LLM
     (Claude / Ollama / Codex) for the residual ambiguous cases.
     Tests inject :func:`fake_classifier` which returns a deterministic
     mode based on the prompt's first keyword.

Output is a :class:`DecisionResult` with mode + confidence + reason
+ source. Worker downstream uses this to choose between
deliberation / research-only / coding-authorization / clarification
prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol, Tuple


# ---------------------------------------------------------------------------
# Mode vocabulary
# ---------------------------------------------------------------------------


MODE_DISCUSSION: str = "discussion"
MODE_RESEARCH_ONLY: str = "research_only"
MODE_IMPLEMENTATION_CANDIDATE: str = "implementation_candidate"
MODE_CLARIFICATION_NEEDED: str = "clarification_needed"


MODES: Tuple[str, ...] = (
    MODE_DISCUSSION,
    MODE_RESEARCH_ONLY,
    MODE_IMPLEMENTATION_CANDIDATE,
    MODE_CLARIFICATION_NEEDED,
)


# Source labels — where the decision came from.
SOURCE_FAST_PATH: str = "fast_path"
SOURCE_CLASSIFIER: str = "classifier"
SOURCE_FALLBACK: str = "fallback"


# ---------------------------------------------------------------------------
# Fast-path keyword tables
# ---------------------------------------------------------------------------


_RESEARCH_KEYWORDS: Tuple[str, ...] = (
    "[research]",
    "조사해줘",
    "리서치만",
    "자료 수집",
    "정리까지만",
    "코드 수정 없이",
    "research only",
    "research-only",
    "research please",
    "참고 자료 찾아",
)


_IMPLEMENTATION_KEYWORDS: Tuple[str, ...] = (
    "구현해줘",
    "구현 해줘",
    "코드 수정해줘",
    "코드 수정 해줘",
    "수정해줘",
    "버그 고쳐",
    "버그 수정",
    "PR 올려",
    "PR 만들어",
    "draft pr",
    "issue 고쳐",
    "리팩터",
    "리팩토링",
    "implement",
    "fix the bug",
    "open a pr",
    "open a draft",
)


_DISCUSSION_KEYWORDS: Tuple[str, ...] = (
    "어떻게 할까",
    "어떻게 해야",
    "결정해줘",
    "의견 좀",
    "토의",
    "회의",
    "방향성",
    "what should we",
    "what do you think",
    "let's discuss",
    "open question",
)


_AMBIGUITY_HINTS: Tuple[str, ...] = (
    "??",
    "혹시",
    "글쎄",
    "잘 모르겠",
    "maybe",
    "not sure",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRequest:
    """One Discord intake / continuation message to route."""

    prompt: str
    session_id: Optional[str] = None
    channel: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    """Verdict on one :class:`DecisionRequest`."""

    mode: str
    confidence: float
    reason: str
    source: str
    matched_keywords: Tuple[str, ...] = ()
    context_pack_id: Optional[str] = None
    routed_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "mode": self.mode,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
            "matched_keywords": list(self.matched_keywords),
            "context_pack_id": self.context_pack_id,
            "routed_at": self.routed_at,
        }


# ---------------------------------------------------------------------------
# Classifier protocol
# ---------------------------------------------------------------------------


class Classifier(Protocol):
    """Optional LLM-backed fallback when fast-path is silent."""

    def classify(
        self, *, request: DecisionRequest, context_pack_id: Optional[str] = None
    ) -> DecisionResult:  # pragma: no cover - Protocol
        ...


def fake_classifier(*, request: DecisionRequest, context_pack_id: Optional[str] = None) -> DecisionResult:
    """Deterministic stub — always returns ``clarification_needed``.

    Useful for tests that want to confirm the fast-path -> classifier
    -> fallback wiring without standing up a real LLM. Production
    wires a richer classifier via the ``classifier=`` argument to
    :func:`route_decision`.
    """

    return DecisionResult(
        mode=MODE_CLARIFICATION_NEEDED,
        confidence=0.5,
        reason="fake classifier — defaulting to clarification_needed",
        source=SOURCE_CLASSIFIER,
        matched_keywords=(),
        context_pack_id=context_pack_id,
        routed_at=_iso_now(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def route_decision(
    request: DecisionRequest,
    *,
    classifier: Optional[Classifier] = None,
    context_pack_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> DecisionResult:
    """Route *request* to one of the 4 modes.

    Stage 1 — fast-path keyword scan. Returns immediately on hit
    with confidence ≥ 0.85 (the higher the more keyword matches).

    Stage 2 — classifier. If the fast-path is silent and a classifier
    is provided, delegate.

    Stage 3 — fallback. If neither fast-path nor classifier produces
    a verdict, return ``clarification_needed`` with confidence 0.4
    so the gateway asks the user.
    """

    when = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    text = (request.prompt or "").strip()
    if not text:
        return DecisionResult(
            mode=MODE_CLARIFICATION_NEEDED,
            confidence=0.4,
            reason="prompt 비어 있음 — 사용자에게 재질의",
            source=SOURCE_FALLBACK,
            matched_keywords=(),
            context_pack_id=context_pack_id,
            routed_at=when,
        )

    fast = _fast_path(text, when=when, context_pack_id=context_pack_id)
    if fast is not None:
        return fast

    if classifier is not None:
        classified = classifier.classify(request=request, context_pack_id=context_pack_id) \
            if hasattr(classifier, "classify") \
            else classifier(request=request, context_pack_id=context_pack_id)  # type: ignore[misc]
        if classified.mode in MODES:
            return classified

    return DecisionResult(
        mode=MODE_CLARIFICATION_NEEDED,
        confidence=0.4,
        reason="fast-path 미스히트 + classifier 없음 — 사용자에게 재질의",
        source=SOURCE_FALLBACK,
        matched_keywords=(),
        context_pack_id=context_pack_id,
        routed_at=when,
    )


# ---------------------------------------------------------------------------
# Fast-path internals
# ---------------------------------------------------------------------------


def _fast_path(
    text: str, *, when: str, context_pack_id: Optional[str]
) -> Optional[DecisionResult]:
    lowered = text.lower()

    research_hits = _scan(lowered, _RESEARCH_KEYWORDS)
    impl_hits = _scan(lowered, _IMPLEMENTATION_KEYWORDS)
    discussion_hits = _scan(lowered, _DISCUSSION_KEYWORDS)
    ambiguous = _scan(lowered, _AMBIGUITY_HINTS)

    # research-only takes precedence — explicit "no code" beats other signals.
    if research_hits and not impl_hits:
        return DecisionResult(
            mode=MODE_RESEARCH_ONLY,
            confidence=_confidence(research_hits),
            reason=f"research-only fast-path hit: {', '.join(research_hits)}",
            source=SOURCE_FAST_PATH,
            matched_keywords=tuple(research_hits),
            context_pack_id=context_pack_id,
            routed_at=when,
        )

    # implementation — run authorization gate downstream.
    if impl_hits and not research_hits:
        return DecisionResult(
            mode=MODE_IMPLEMENTATION_CANDIDATE,
            confidence=_confidence(impl_hits),
            reason=f"implementation fast-path hit: {', '.join(impl_hits)}",
            source=SOURCE_FAST_PATH,
            matched_keywords=tuple(impl_hits),
            context_pack_id=context_pack_id,
            routed_at=when,
        )

    # both present — conflict, fall through to classifier / fallback.
    if research_hits and impl_hits:
        return DecisionResult(
            mode=MODE_CLARIFICATION_NEEDED,
            confidence=0.5,
            reason=(
                "research-only + implementation 키워드 동시 발견 — 사용자 확인 필요 "
                f"(research={research_hits}, impl={impl_hits})"
            ),
            source=SOURCE_FAST_PATH,
            matched_keywords=tuple(research_hits + impl_hits),
            context_pack_id=context_pack_id,
            routed_at=when,
        )

    # discussion mode (lower confidence — easy to confuse with normal chatter).
    if discussion_hits and not ambiguous:
        return DecisionResult(
            mode=MODE_DISCUSSION,
            confidence=_confidence(discussion_hits, base=0.7),
            reason=f"discussion fast-path hit: {', '.join(discussion_hits)}",
            source=SOURCE_FAST_PATH,
            matched_keywords=tuple(discussion_hits),
            context_pack_id=context_pack_id,
            routed_at=when,
        )

    return None


def _scan(text: str, keywords: Tuple[str, ...]) -> list:
    return [kw for kw in keywords if kw.lower() in text]


def _confidence(hits: list, *, base: float = 0.85) -> float:
    # 1+ hit → base; 2 → +0.05; 3+ → +0.10. Cap at 0.99.
    bump = min(0.10, 0.05 * max(0, len(hits) - 1))
    return min(0.99, base + bump)


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "Classifier",
    "DecisionRequest",
    "DecisionResult",
    "MODES",
    "MODE_CLARIFICATION_NEEDED",
    "MODE_DISCUSSION",
    "MODE_IMPLEMENTATION_CANDIDATE",
    "MODE_RESEARCH_ONLY",
    "SOURCE_CLASSIFIER",
    "SOURCE_FALLBACK",
    "SOURCE_FAST_PATH",
    "fake_classifier",
    "route_decision",
)
