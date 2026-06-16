"""Render a ProductIntentPacket for the two consumer surfaces.

  * :func:`clarification_lines` — the ≤3 PM decision questions, option-shaped with
    a recommended answer, for the user when state is ``clarification_needed``.
  * :func:`handoff_summary` — the structured packet summary (decisions, implied
    features, acceptance, non-goals) carried to tech-lead / engineering so they
    work off the spec, not the raw request.
  * :func:`operator_status_line` — a one-liner for the operator surface that
    distinguishes PM clarification from engineering-handoff-ready.
"""

from __future__ import annotations

from typing import Tuple

from .models import (
    READINESS_CLARIFICATION,
    READINESS_IMPLEMENTATION_CANDIDATE,
    READINESS_RESEARCH_ONLY,
    READINESS_SPEC_READY,
    ProductIntentPacket,
)


def clarification_lines(packet: ProductIntentPacket) -> Tuple[str, ...]:
    """The user-facing PM questions (numbered options + a recommended pick)."""

    lines: list[str] = []
    for i, q in enumerate(packet.decision_questions, start=1):
        lines.append(f"{i}. {q.prompt}")
        for j, opt in enumerate(q.options, start=1):
            tag = " (추천)" if opt.recommended else ""
            lines.append(f"   {j}. {opt.label}{tag}")
    return tuple(lines)


def handoff_summary(packet: ProductIntentPacket) -> Tuple[str, ...]:
    """The structured packet summary for tech-lead / engineering handoff."""

    lines = [
        f"product packet — {packet.user_goal}",
        f"target: {packet.target_user}",
        f"families: {', '.join(packet.detected_families) or '(none)'}",
        f"readiness: {packet.readiness.readiness}",
        "",
        "core flow: " + (" → ".join(packet.core_flow) or "(n/a)"),
    ]
    if packet.decision_questions:
        lines.append("user decisions (pending):")
        lines.extend(f"  - {q.prompt}" for q in packet.decision_questions)
    if packet.implied_features:
        lines.append("implied features (auto-added):")
        lines.extend(f"  - {g.name}" for g in packet.implied_features)
    if packet.acceptance_criteria:
        lines.append("acceptance criteria:")
        lines.extend(f"  - {c}" for c in packet.acceptance_criteria)
    if packet.non_goals:
        lines.append("non-goals:")
        lines.extend(f"  - {n}" for n in packet.non_goals)
    lines.append("suggested roles: " + ", ".join(packet.suggested_roles))
    return tuple(lines)


def operator_status_line(packet: ProductIntentPacket) -> str:
    """One-liner distinguishing PM clarification vs engineering-handoff-ready."""

    state = packet.readiness.readiness
    if state == READINESS_CLARIFICATION:
        n = len(packet.decision_questions)
        return f"PM clarification — 결정 질문 {n}개 대기 (기획 누락 보강)"
    if state in (READINESS_SPEC_READY, READINESS_IMPLEMENTATION_CANDIDATE):
        return "engineering handoff ready — product packet 전달 가능"
    if state == READINESS_RESEARCH_ONLY:
        return "research only — 조사/분석 요청"
    return f"product intake: {state}"


__all__ = ("clarification_lines", "handoff_summary", "operator_status_line")
