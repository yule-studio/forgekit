"""Operator-readable surface for the role mistake ledger — issue #81.

The ledger + preflight + hook-candidate modules each own a piece of
the data; the operator surface joins them into one short summary
that runtime/status renderers and completion metadata can splice
without re-importing each producer.

This module deliberately stays text-only. The runtime status
renderer in :mod:`runtime.status` already owns the markdown / JSON
shape for service health and completion-funnel rows; mistake-ledger
content joins as a string block so we don't have to widen
:class:`RuntimeStatusReport` (and its many tests) to land round 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .hook_candidate import (
    HookCandidate,
    collect_hook_candidates,
    render_hook_candidate_block,
)
from .mistake_ledger import (
    MistakeRecord,
    RoleMistakeSummary,
    read_mistake_ledger,
    summarize_role_mistakes,
)
from .preflight_judgement import (
    PREFLIGHT_PASS,
    PreflightAdvisory,
    render_preflight_advisory_block,
)


@dataclass(frozen=True)
class MistakeOperatorSurface:
    """Joined view: per-role summaries + preflight + hook candidates.

    Each producer can build this once per surface tick (status post,
    completion-funnel stamp, gateway response) and either render it
    or persist its payload alongside the existing audit / funnel
    blocks.
    """

    summaries: Tuple[RoleMistakeSummary, ...]
    preflight: Optional[PreflightAdvisory] = None
    hook_candidates: Tuple[HookCandidate, ...] = ()

    def is_empty(self) -> bool:
        if self.summaries:
            return False
        if self.hook_candidates:
            return False
        if self.preflight is not None and self.preflight.has_signal():
            return False
        return True

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "summaries": [s.to_payload() for s in self.summaries],
            "preflight": (
                self.preflight.to_payload()
                if self.preflight is not None
                else None
            ),
            "hook_candidates": [c.to_payload() for c in self.hook_candidates],
            "summary_line": self.summary_line(),
        }

    def summary_line(self) -> str:
        """One-line digest — fits runtime/status banners and funnel rows.

        Returns the empty string when there is no signal so producers
        can append unconditionally without growing the surface. Shape:

          ``"역할 N건 (총 M회) · preflight=<verdict> · hook 후보 K건"``

        The preflight clause is dropped when no advisory is attached or
        when it didn't trigger; the hook-candidate clause is dropped
        when there are no candidates. The role-count clause is dropped
        when the ledger is empty.
        """

        parts: list[str] = []
        if self.summaries:
            occurrences = sum(s.total_occurrences for s in self.summaries)
            parts.append(
                f"역할 {len(self.summaries)}건 (총 {occurrences}회)"
            )
        if self.preflight is not None and self.preflight.has_signal():
            parts.append(f"preflight={self.preflight.verdict}")
        if self.hook_candidates:
            parts.append(f"hook 후보 {len(self.hook_candidates)}건")
        return " · ".join(parts)


def build_operator_surface(
    source: Any,
    *,
    preflight: Optional[PreflightAdvisory] = None,
    min_evidence: int = 2,
    top_recurring: int = 3,
) -> MistakeOperatorSurface:
    """Project the ledger on *source* into the operator-friendly view.

    *preflight* is optional — supply it when the surface tick coincides
    with a role-specific preflight evaluation (e.g. before a
    coding-execute dispatch). Otherwise the surface still renders the
    per-role summary + hook candidates, just without the preflight
    headline.

    *min_evidence* propagates to :func:`collect_hook_candidates` so
    callers can decide how aggressive the "this should become a hook"
    suggestion is. Default 2 matches the candidate module's policy.
    """

    summaries = summarize_role_mistakes(source, top_recurring=top_recurring)
    records = read_mistake_ledger(source)
    candidates = collect_hook_candidates(records, min_evidence=min_evidence)
    return MistakeOperatorSurface(
        summaries=summaries,
        preflight=preflight,
        hook_candidates=candidates,
    )


def render_operator_surface_block(
    surface: MistakeOperatorSurface,
    *,
    title: str = "역할별 반복 실수",
) -> str:
    """Render *surface* as a short, copy-pasteable text block.

    Returns the empty string when *surface* has no signal so callers
    can append unconditionally to a status post / funnel summary
    without growing the surface.
    """

    if surface.is_empty():
        return ""

    lines: list[str] = [f"### {title}"]
    if not surface.summaries:
        lines.append("  (역할별 반복 실수 기록 없음)")
    else:
        for summary in surface.summaries:
            lines.append(_format_role_summary_line(summary))
            for record in summary.top_recurring:
                lines.append(_format_top_record_line(record))

    if (
        surface.preflight is not None
        and surface.preflight.verdict != PREFLIGHT_PASS
    ):
        block = render_preflight_advisory_block(surface.preflight)
        if block:
            lines.append("")
            lines.append("preflight 판정:")
            for chunk in block.split("\n"):
                lines.append("  " + chunk if chunk else "")

    if surface.hook_candidates:
        lines.append("")
        block = render_hook_candidate_block(surface.hook_candidates)
        if block:
            for chunk in block.split("\n"):
                lines.append(chunk)

    return "\n".join(lines).rstrip()


def _format_role_summary_line(summary: RoleMistakeSummary) -> str:
    return (
        f"- `{summary.role_id}` — 누적 실수 {summary.total_mistakes}건 "
        f"({summary.total_occurrences}회 발생, "
        f"high={summary.high_severity_count} med={summary.medium_severity_count} "
        f"low={summary.low_severity_count})"
    )


def _format_top_record_line(record: MistakeRecord) -> str:
    return (
        f"    · `{record.mistake_key}` x{record.occurrence_count} "
        f"({record.severity}, {record.source_kind}) — {record.summary}"
    )


__all__ = (
    "MistakeOperatorSurface",
    "build_operator_surface",
    "render_operator_surface_block",
)
