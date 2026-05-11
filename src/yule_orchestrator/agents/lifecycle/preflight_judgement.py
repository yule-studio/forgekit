"""Preflight judgement seam — issue #81 round 1.

Hook point that fires *just before* a role-scoped action so the agent
can surface "you've made this mistake before" warnings (or, for
critical patterns, refuse to start the action). Reads from the
session-extras ledger built by :mod:`agents.lifecycle.mistake_ledger`
— no DB, no external state.

The seam intentionally returns one of four verdicts so callers can
ladder the response (e.g. coding-execute pre-dispatch can pass advisories
through to the operator surface but block on a high verdict, while
discussion handoff might just record the advisory and proceed).

Threshold rules (default; producers can override via
:class:`PreflightThresholds`):

  * ``high`` severity AND ``occurrence_count >= 3`` → block
  * ``occurrence_count >= 5`` → block (any severity — 5x is enough
    evidence even for a low-severity mistake)
  * ``occurrence_count >= 3`` → warning
  * ``occurrence_count >= 2`` → advisory
  * else → pass

Hard rails (secret access / branch protection / merge) live in
:mod:`agents.lifecycle.autonomy_policy` — this seam is purely about
*recurring role mistakes*, not first-time destructive guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .mistake_ledger import (
    MistakeRecord,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    mistakes_for_role,
)


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------


PREFLIGHT_PASS: str = "pass"
PREFLIGHT_ADVISORY: str = "advisory"
PREFLIGHT_WARNING: str = "warning"
PREFLIGHT_BLOCK: str = "block"


PREFLIGHT_VERDICTS: Tuple[str, ...] = (
    PREFLIGHT_PASS,
    PREFLIGHT_ADVISORY,
    PREFLIGHT_WARNING,
    PREFLIGHT_BLOCK,
)


_VERDICT_ORDER: Mapping[str, int] = {
    PREFLIGHT_PASS: 0,
    PREFLIGHT_ADVISORY: 1,
    PREFLIGHT_WARNING: 2,
    PREFLIGHT_BLOCK: 3,
}


def _max_verdict(*verdicts: str) -> str:
    return max(verdicts, key=lambda v: _VERDICT_ORDER.get(v, 0))


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightThresholds:
    """Threshold curve a role / action can override.

    Defaults match the policy in the module docstring.
    """

    advisory_at: int = 2
    warning_at: int = 3
    block_at: int = 5
    high_severity_block_at: int = 3

    def verdict_for(self, *, occurrence_count: int, severity: str) -> str:
        if (
            severity == SEVERITY_HIGH
            and occurrence_count >= max(1, int(self.high_severity_block_at))
        ):
            return PREFLIGHT_BLOCK
        if occurrence_count >= max(1, int(self.block_at)):
            return PREFLIGHT_BLOCK
        if occurrence_count >= max(1, int(self.warning_at)):
            return PREFLIGHT_WARNING
        if occurrence_count >= max(1, int(self.advisory_at)):
            return PREFLIGHT_ADVISORY
        return PREFLIGHT_PASS


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightAdvisory:
    """Result of one preflight evaluation.

    ``verdict`` is the highest verdict triggered by any matched
    mistake. ``triggered_mistakes`` is the subset of role-mistakes
    that contributed to the verdict (i.e. produced an
    advisory-or-stronger). ``checklist`` are the human-readable
    prevention hints the producer should surface to the operator (or
    the next worker prompt).
    """

    verdict: str
    role_id: str
    action: str
    triggered_mistakes: Tuple[MistakeRecord, ...] = ()
    checklist: Tuple[str, ...] = ()
    headline: str = ""

    def is_block(self) -> bool:
        return self.verdict == PREFLIGHT_BLOCK

    def has_signal(self) -> bool:
        """True when the verdict isn't a clean pass."""

        return self.verdict != PREFLIGHT_PASS

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "verdict": self.verdict,
            "role_id": self.role_id,
            "action": self.action,
            "headline": self.headline,
            "triggered_mistake_keys": [
                m.mistake_key for m in self.triggered_mistakes
            ],
            "checklist": list(self.checklist),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_preflight(
    source: Any,
    *,
    role_id: str,
    action: str,
    thresholds: Optional[PreflightThresholds] = None,
    only_keys: Optional[Sequence[str]] = None,
) -> PreflightAdvisory:
    """Evaluate the role-mistake ledger against an upcoming *action*.

    *source* may be either a session-shaped object (with ``.extra``)
    or a raw extra mapping. *only_keys* lets the caller scope the
    evaluation to a specific subset of mistake keys (e.g. only
    ``ci:`` mistakes for a coding-executor preflight); the default
    considers every mistake recorded against the role.

    The returned advisory is always non-None, even when no mistake
    triggered — callers can branch on
    :meth:`PreflightAdvisory.has_signal`.
    """

    thresholds = thresholds or PreflightThresholds()
    role_id = str(role_id or "").strip()
    action = str(action or "").strip()

    records = mistakes_for_role(source, role_id) if role_id else ()
    if only_keys:
        keep = {k for k in only_keys if k}
        records = tuple(r for r in records if r.mistake_key in keep)

    triggered: list[MistakeRecord] = []
    final_verdict = PREFLIGHT_PASS
    for record in records:
        verdict = thresholds.verdict_for(
            occurrence_count=record.occurrence_count,
            severity=record.severity,
        )
        if verdict == PREFLIGHT_PASS:
            continue
        triggered.append(record)
        final_verdict = _max_verdict(final_verdict, verdict)

    triggered_sorted = tuple(
        sorted(
            triggered,
            key=lambda r: (-r.occurrence_count, r.mistake_key),
        )
    )
    checklist = tuple(
        _format_checklist_item(record) for record in triggered_sorted
    )
    headline = _build_headline(
        verdict=final_verdict,
        role_id=role_id,
        action=action,
        triggered=triggered_sorted,
    )
    return PreflightAdvisory(
        verdict=final_verdict,
        role_id=role_id,
        action=action,
        triggered_mistakes=triggered_sorted,
        checklist=checklist,
        headline=headline,
    )


def render_preflight_advisory_block(advisory: PreflightAdvisory) -> str:
    """Compact, operator-friendly text summary.

    Returns the empty string when *advisory* has no signal so the
    caller can append unconditionally without growing the surface.
    """

    if not advisory.has_signal():
        return ""

    lines: list[str] = [advisory.headline]
    for item in advisory.checklist:
        lines.append(f"  - {item}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_VERDICT_ICON: Mapping[str, str] = {
    PREFLIGHT_ADVISORY: "💡",
    PREFLIGHT_WARNING: "⚠️",
    PREFLIGHT_BLOCK: "⛔",
}


def _build_headline(
    *,
    verdict: str,
    role_id: str,
    action: str,
    triggered: Sequence[MistakeRecord],
) -> str:
    if verdict == PREFLIGHT_PASS:
        return ""
    icon = _VERDICT_ICON.get(verdict, "•")
    role_part = f"`{role_id}`" if role_id else "(role 미지정)"
    action_part = f"`{action}`" if action else "(action 미지정)"
    count = len(triggered)
    label = {
        PREFLIGHT_ADVISORY: "advisory",
        PREFLIGHT_WARNING: "warning",
        PREFLIGHT_BLOCK: "block",
    }.get(verdict, verdict)
    return (
        f"{icon} preflight {label} — {role_part} 가 {action_part} 진입 전, "
        f"이전에 반복된 실수 {count}건을 확인하라"
    )


def _format_checklist_item(record: MistakeRecord) -> str:
    severity_tag = {
        SEVERITY_HIGH: "[high]",
        SEVERITY_MEDIUM: "[med]",
        SEVERITY_LOW: "[low]",
    }.get(record.severity, "[?]")
    hint = record.prevention_hint.strip()
    summary = record.summary.strip()
    base = (
        f"{severity_tag} `{record.mistake_key}` ({record.occurrence_count}회) "
        f"— {summary}"
    )
    if hint:
        base += f" · 예방: {hint}"
    return base


__all__ = (
    "PREFLIGHT_ADVISORY",
    "PREFLIGHT_BLOCK",
    "PREFLIGHT_PASS",
    "PREFLIGHT_VERDICTS",
    "PREFLIGHT_WARNING",
    "PreflightAdvisory",
    "PreflightThresholds",
    "evaluate_preflight",
    "render_preflight_advisory_block",
)
