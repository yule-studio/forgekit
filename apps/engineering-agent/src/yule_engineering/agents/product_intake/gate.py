"""Product intake gate — the seam the engineering gateway calls *before* shaping.

Additive, not a rewrite: the gateway asks :func:`run_product_gate` whether a
request is a product/feature ask. If not, it returns ``intercepted=False`` and
the existing engineering clarification flow proceeds unchanged. If it is, the PM
shapes a :class:`ProductIntentPacket` and the outcome carries either *PM*
clarification questions (distinct from engineering's technical clarification) or
a structured handoff for tech-lead — so engineering moves on the packet, not the
raw request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from . import families as fam
from .models import (
    READINESS_CLARIFICATION,
    READINESS_IMPLEMENTATION_CANDIDATE,
    READINESS_SPEC_READY,
    ProductIntentPacket,
)
from .shaping import shape_product_intent

# Build/feature verbs that (with or without a family hit) make a request a
# product ask the PM should shape.
_BUILD_MARKERS: Tuple[str, ...] = (
    "구현", "만들", "추가", "개발", "기능", "서비스", "페이지", "화면",
    "build", "implement", "feature", "add ", "create", "service",
)


def should_intercept(raw_text: str) -> bool:
    """True when the request is a product/feature ask (else engineering proceeds)."""

    text = (raw_text or "").strip().lower()
    if not text:
        return False
    if fam.detect_families(text):
        return True
    return any(m in text for m in _BUILD_MARKERS)


@dataclass(frozen=True)
class ProductIntakeOutcome:
    """What the gate hands back to the gateway."""

    intercepted: bool
    state: str = ""
    packet: Optional[ProductIntentPacket] = None
    clarification_questions: Tuple[str, ...] = ()  # rendered, user-facing
    handoff_ready: bool = False

    def to_dict(self) -> dict:
        return {
            "intercepted": self.intercepted,
            "state": self.state,
            "handoff_ready": self.handoff_ready,
            "clarification_questions": list(self.clarification_questions),
            "packet": self.packet.to_dict() if self.packet else None,
        }


def run_product_gate(
    raw_text: str, *, metadata: Optional[Mapping[str, object]] = None
) -> ProductIntakeOutcome:
    """Run the PM intake gate. Non-product asks pass through untouched."""

    if not should_intercept(raw_text):
        return ProductIntakeOutcome(intercepted=False)

    packet = shape_product_intent(raw_text, metadata=metadata)
    state = packet.readiness.readiness
    from .presenter import clarification_lines

    handoff_ready = state in (READINESS_SPEC_READY, READINESS_IMPLEMENTATION_CANDIDATE)
    questions = clarification_lines(packet) if state == READINESS_CLARIFICATION else ()
    return ProductIntakeOutcome(
        intercepted=True,
        state=state,
        packet=packet,
        clarification_questions=questions,
        handoff_ready=handoff_ready,
    )


__all__ = ("should_intercept", "ProductIntakeOutcome", "run_product_gate")
