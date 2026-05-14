"""Growth loop capture ledger — P0-I stage 3 (#141).

Implements stage-1 policy ``policies/runtime/agents/engineering-agent/growth-loop.md``
in code form. The *signal detection* layer (``self_improvement.py``) was
already land; this module adds the *capture* layer:

  * record references consulted during implementation,
  * record retrospectives / "다음엔 이렇게 더 나아질 수 있음" notes,
  * surface repeated patterns as promotion candidates (개인 메모 → 정책).

The ledger is append-only and lives in ``session.extra["growth_ledger"]``
as a list of plain dicts (round-trips via SQLite payload). Each entry
carries enough context that the gateway's status surface + the future
auto-promotion helper (#141 후속) can reason about it.

Promotion rule (stage-1 §3): when ≥2 distinct signal kinds repeat
or the same `pattern_tag` shows up ≥3 times, it's promoted to a
candidate. We only emit *candidates* — caller decides whether to
open a policy PR.

Event kinds:
  * ``reference_used`` — consulted external repo / docs / PR / vault note.
  * ``decision_made`` — role / mode / scope decision recorded.
  * ``regret`` / ``retrospective`` — "이번엔 이랬는데 다음엔 이렇게".
  * ``risk_surfaced`` — blocker or warning surfaced for the user.
  * ``pattern_observed`` — caller noticed a repeating shape worth tagging.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


EVENT_REFERENCE_USED = "reference_used"
EVENT_DECISION_MADE = "decision_made"
EVENT_REGRET = "regret"
EVENT_RETROSPECTIVE = "retrospective"
EVENT_RISK_SURFACED = "risk_surfaced"
EVENT_PATTERN_OBSERVED = "pattern_observed"

EVENT_KINDS = (
    EVENT_REFERENCE_USED,
    EVENT_DECISION_MADE,
    EVENT_REGRET,
    EVENT_RETROSPECTIVE,
    EVENT_RISK_SURFACED,
    EVENT_PATTERN_OBSERVED,
)


# Stage-1 §3 — patterns repeating ≥3 times become promotion candidates.
_PROMOTION_REPEAT_THRESHOLD = 3
# At least 2 distinct kinds of signals must fire before we consider
# promotion (stage-1 §3 explicit). Single-kind repetition is not enough.
_PROMOTION_DISTINCT_KIND_THRESHOLD = 2


@dataclass(frozen=True)
class GrowthEvent:
    """One captured growth-loop event.

    Always carries ``kind`` + ``summary``. Optional fields enrich
    the audit:
      * ``pattern_tag`` — caller-supplied key. Promotion counts
        match by this tag (e.g. ``"forum-followup-no-session"``).
      * ``source_url`` — GitHub / Obsidian / external link.
      * ``role`` — who emitted the event (``tech-lead`` / ``frontend-engineer`` / ``gateway`` / etc.).
      * ``severity`` — ``info`` / ``minor`` / ``major``. Used by the
        renderer to sort or filter.
    """

    kind: str
    summary: str
    pattern_tag: Optional[str] = None
    source_url: Optional[str] = None
    role: Optional[str] = None
    severity: str = "info"
    recorded_at: Optional[str] = None  # iso8601 UTC

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "pattern_tag": self.pattern_tag,
            "source_url": self.source_url,
            "role": self.role,
            "severity": self.severity,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GrowthEvent":
        return cls(
            kind=str(payload.get("kind") or ""),
            summary=str(payload.get("summary") or ""),
            pattern_tag=_coerce_optional_str(payload.get("pattern_tag")),
            source_url=_coerce_optional_str(payload.get("source_url")),
            role=_coerce_optional_str(payload.get("role")),
            severity=str(payload.get("severity") or "info"),
            recorded_at=_coerce_optional_str(payload.get("recorded_at")),
        )


@dataclass(frozen=True)
class PromotionCandidate:
    """A signal that's appeared often enough to consider policy promotion."""

    pattern_tag: str
    occurrence_count: int
    distinct_kinds: Tuple[str, ...]
    sample_summaries: Tuple[str, ...]

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "pattern_tag": self.pattern_tag,
            "occurrence_count": self.occurrence_count,
            "distinct_kinds": list(self.distinct_kinds),
            "sample_summaries": list(self.sample_summaries),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def append_growth_event(
    extra: dict,
    event: GrowthEvent,
    *,
    now: Optional[datetime] = None,
) -> GrowthEvent:
    """Append *event* to ``extra["growth_ledger"]``.

    Mutates *extra* in place. Returns the persisted event (with
    ``recorded_at`` stamped if it wasn't set). Never raises.
    """

    stamped_event = (
        GrowthEvent(
            kind=event.kind,
            summary=event.summary,
            pattern_tag=event.pattern_tag,
            source_url=event.source_url,
            role=event.role,
            severity=event.severity,
            recorded_at=event.recorded_at or _now_iso(now),
        )
    )
    ledger = list(extra.get("growth_ledger") or ())
    ledger.append(dict(stamped_event.to_dict()))
    extra["growth_ledger"] = ledger
    # Recompute promotion candidates after each append.
    extra["growth_promotion_candidates"] = [
        c.to_dict() for c in compute_promotion_candidates(extra)
    ]
    return stamped_event


def read_ledger(extra: Mapping[str, Any]) -> Tuple[GrowthEvent, ...]:
    """Return all captured events for *extra* (most recent last)."""

    raw = extra.get("growth_ledger") if isinstance(extra, Mapping) else None
    if not raw:
        return ()
    out: list = []
    for entry in raw:
        if isinstance(entry, Mapping):
            out.append(GrowthEvent.from_dict(entry))
    return tuple(out)


def compute_promotion_candidates(
    extra: Mapping[str, Any],
    *,
    repeat_threshold: int = _PROMOTION_REPEAT_THRESHOLD,
    distinct_kinds_threshold: int = _PROMOTION_DISTINCT_KIND_THRESHOLD,
) -> Tuple[PromotionCandidate, ...]:
    """Return promotion candidates from the ledger.

    Rules (stage-1 growth-loop.md §3):

      * Same ``pattern_tag`` appears ≥ *repeat_threshold* times → candidate.
      * OR ≥ *distinct_kinds_threshold* distinct ``kind`` values share
        the same pattern_tag.

    Events without ``pattern_tag`` are ignored — promotion requires
    explicit tagging (caller decided the pattern is worth naming).
    """

    events = read_ledger(extra)
    by_tag: dict = {}
    for event in events:
        if not event.pattern_tag:
            continue
        bucket = by_tag.setdefault(
            event.pattern_tag,
            {"events": [], "kinds": set()},
        )
        bucket["events"].append(event)
        bucket["kinds"].add(event.kind)

    out: list[PromotionCandidate] = []
    for tag, bucket in by_tag.items():
        events_for_tag = bucket["events"]
        kinds_for_tag = bucket["kinds"]
        meets_repeat = len(events_for_tag) >= repeat_threshold
        meets_diverse = len(kinds_for_tag) >= distinct_kinds_threshold
        if not (meets_repeat or meets_diverse):
            continue
        samples = tuple(e.summary for e in events_for_tag[:3])
        out.append(
            PromotionCandidate(
                pattern_tag=tag,
                occurrence_count=len(events_for_tag),
                distinct_kinds=tuple(sorted(kinds_for_tag)),
                sample_summaries=samples,
            )
        )
    out.sort(key=lambda c: (-c.occurrence_count, c.pattern_tag))
    return tuple(out)


def summarize_for_status(extra: Mapping[str, Any]) -> Optional[str]:
    """Return a one-line Korean status summary, or None when empty.

    Format examples:
      * "🌱 growth ledger: references 2 · decisions 1 · risks 1"
      * "🌱 growth ledger: 4 events · promotion 후보 1건 (pattern_tag=...)"
    """

    events = read_ledger(extra)
    if not events:
        return None
    by_kind: dict = {}
    for event in events:
        by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
    parts: list = []
    label_map = {
        EVENT_REFERENCE_USED: "references",
        EVENT_DECISION_MADE: "decisions",
        EVENT_REGRET: "regrets",
        EVENT_RETROSPECTIVE: "retros",
        EVENT_RISK_SURFACED: "risks",
        EVENT_PATTERN_OBSERVED: "patterns",
    }
    for kind in EVENT_KINDS:
        count = by_kind.get(kind, 0)
        if count:
            parts.append(f"{label_map.get(kind, kind)} {count}")
    candidates = compute_promotion_candidates(extra)
    suffix = ""
    if candidates:
        first_tag = candidates[0].pattern_tag
        suffix = (
            f" · promotion 후보 {len(candidates)}건 (pattern_tag=`{first_tag}`)"
        )
    if not parts:
        return f"🌱 growth ledger: {len(events)} events{suffix}"
    return f"🌱 growth ledger: " + " · ".join(parts) + suffix


# ---------------------------------------------------------------------------
# Builders for the common event types — keep callers terse.
# ---------------------------------------------------------------------------


def build_reference_event(
    *,
    summary: str,
    source_url: Optional[str] = None,
    pattern_tag: Optional[str] = None,
    role: Optional[str] = None,
) -> GrowthEvent:
    return GrowthEvent(
        kind=EVENT_REFERENCE_USED,
        summary=summary,
        source_url=source_url,
        pattern_tag=pattern_tag,
        role=role,
    )


def build_retrospective_event(
    *,
    summary: str,
    pattern_tag: Optional[str] = None,
    role: Optional[str] = None,
    severity: str = "info",
) -> GrowthEvent:
    return GrowthEvent(
        kind=EVENT_RETROSPECTIVE,
        summary=summary,
        pattern_tag=pattern_tag,
        role=role,
        severity=severity,
    )


def build_regret_event(
    *,
    summary: str,
    pattern_tag: Optional[str] = None,
    role: Optional[str] = None,
) -> GrowthEvent:
    return GrowthEvent(
        kind=EVENT_REGRET,
        summary=summary,
        pattern_tag=pattern_tag,
        role=role,
        severity="minor",
    )


def build_risk_event(
    *,
    summary: str,
    pattern_tag: Optional[str] = None,
    severity: str = "major",
    role: Optional[str] = None,
) -> GrowthEvent:
    return GrowthEvent(
        kind=EVENT_RISK_SURFACED,
        summary=summary,
        pattern_tag=pattern_tag,
        severity=severity,
        role=role,
    )


def build_decision_event(
    *,
    summary: str,
    pattern_tag: Optional[str] = None,
    role: Optional[str] = None,
) -> GrowthEvent:
    return GrowthEvent(
        kind=EVENT_DECISION_MADE,
        summary=summary,
        pattern_tag=pattern_tag,
        role=role,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _now_iso(now: Optional[datetime]) -> str:
    moment = now or datetime.now(tz=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = (
    "EVENT_DECISION_MADE",
    "EVENT_KINDS",
    "EVENT_PATTERN_OBSERVED",
    "EVENT_REFERENCE_USED",
    "EVENT_REGRET",
    "EVENT_RETROSPECTIVE",
    "EVENT_RISK_SURFACED",
    "GrowthEvent",
    "PromotionCandidate",
    "append_growth_event",
    "build_decision_event",
    "build_reference_event",
    "build_regret_event",
    "build_retrospective_event",
    "build_risk_event",
    "compute_promotion_candidates",
    "read_ledger",
    "summarize_for_status",
)
