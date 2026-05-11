"""Role-specific mistake ledger — issue #81 round 1.

Hookify the engineering-agent runtime by surfacing repeated role-level
mistakes (CI failure / blocked completion / postmortem reason) so the
preflight judgement seam can warn or block before the same mistake is
attempted again.

Storage model — round 1 stage:

  * Records land on ``session.extra['role_mistake_ledger']`` as a list
    of payload-friendly dicts so a single SQLite write of the existing
    session row carries the ledger forward.
  * **No persistent DB yet** — by design (see issue #81). Once we have
    a stable contract, a follow-up can lift the same shape into a
    cross-session table without changing producer call sites.

The ledger is keyed by ``(role_id, mistake_key)``: re-recording the
same mistake increments ``occurrence_count`` and bumps
``last_seen_at`` instead of appending a new row. That keeps the
ledger small even on long-lived sessions and makes
``preflight_judgement.evaluate_preflight`` cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple


SESSION_EXTRA_KEY: str = "role_mistake_ledger"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


SOURCE_CI_FAILURE: str = "ci_failure"
SOURCE_BLOCKED_COMPLETION: str = "blocked_completion"
SOURCE_POSTMORTEM: str = "postmortem"
SOURCE_MANUAL_NOTE: str = "manual_note"

SOURCE_KINDS: Tuple[str, ...] = (
    SOURCE_CI_FAILURE,
    SOURCE_BLOCKED_COMPLETION,
    SOURCE_POSTMORTEM,
    SOURCE_MANUAL_NOTE,
)


SEVERITY_LOW: str = "low"
SEVERITY_MEDIUM: str = "medium"
SEVERITY_HIGH: str = "high"

SEVERITIES: Tuple[str, ...] = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH)


_SEVERITY_ORDER: Mapping[str, int] = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
}


def _normalise_severity(value: Any) -> str:
    raw = (str(value or "").strip() or SEVERITY_LOW).lower()
    if raw not in _SEVERITY_ORDER:
        return SEVERITY_LOW
    return raw


def _normalise_source_kind(value: Any) -> str:
    raw = (str(value or "").strip() or SOURCE_MANUAL_NOTE).lower()
    if raw not in SOURCE_KINDS:
        return SOURCE_MANUAL_NOTE
    return raw


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MistakeRecord:
    """One ``(role_id, mistake_key)`` row in the ledger.

    The dataclass is frozen so producers can pass it across boundaries
    without worrying about accidental mutation. Mutation = build a new
    record via :meth:`bump`.
    """

    role_id: str
    mistake_key: str
    summary: str
    severity: str
    prevention_hint: str
    source_kind: str
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int = 1

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "role_id": self.role_id,
            "mistake_key": self.mistake_key,
            "summary": self.summary,
            "severity": self.severity,
            "prevention_hint": self.prevention_hint,
            "source_kind": self.source_kind,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "occurrence_count": self.occurrence_count,
        }

    @classmethod
    def from_payload(
        cls, data: Optional[Mapping[str, Any]]
    ) -> Optional["MistakeRecord"]:
        if not isinstance(data, Mapping):
            return None
        role_id = str(data.get("role_id") or "").strip()
        mistake_key = str(data.get("mistake_key") or "").strip()
        if not role_id or not mistake_key:
            return None
        try:
            count = int(data.get("occurrence_count") or 1)
        except (TypeError, ValueError):
            count = 1
        return cls(
            role_id=role_id,
            mistake_key=mistake_key,
            summary=str(data.get("summary") or ""),
            severity=_normalise_severity(data.get("severity")),
            prevention_hint=str(data.get("prevention_hint") or ""),
            source_kind=_normalise_source_kind(data.get("source_kind")),
            first_seen_at=str(data.get("first_seen_at") or ""),
            last_seen_at=str(data.get("last_seen_at") or ""),
            occurrence_count=max(1, count),
        )

    def bump(
        self,
        *,
        when: str,
        summary: Optional[str] = None,
        prevention_hint: Optional[str] = None,
        severity: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> "MistakeRecord":
        """Return a new record with ``occurrence_count`` incremented.

        Severity can only escalate (low → medium → high); a recurring
        mistake whose new evidence is *milder* keeps the previous
        severity so the ledger never silently relaxes a prior risk
        signal. Summary / prevention_hint update opportunistically:
        the latest non-empty value wins.
        """

        next_severity = self.severity
        if severity is not None:
            normalised = _normalise_severity(severity)
            if _SEVERITY_ORDER[normalised] > _SEVERITY_ORDER[self.severity]:
                next_severity = normalised
        return MistakeRecord(
            role_id=self.role_id,
            mistake_key=self.mistake_key,
            summary=(summary or self.summary).strip() or self.summary,
            severity=next_severity,
            prevention_hint=(
                (prevention_hint or self.prevention_hint).strip()
                or self.prevention_hint
            ),
            source_kind=(
                _normalise_source_kind(source_kind)
                if source_kind is not None
                else self.source_kind
            ),
            first_seen_at=self.first_seen_at,
            last_seen_at=when,
            occurrence_count=self.occurrence_count + 1,
        )


# ---------------------------------------------------------------------------
# Persistence helpers (session.extra round trip)
# ---------------------------------------------------------------------------


def record_mistake(
    extra: Optional[Mapping[str, Any]],
    *,
    role_id: str,
    mistake_key: str,
    summary: str,
    prevention_hint: str,
    source_kind: str = SOURCE_MANUAL_NOTE,
    severity: str = SEVERITY_LOW,
    when: Optional[str] = None,
    max_entries: int = 64,
) -> Tuple[Mapping[str, Any], MistakeRecord]:
    """Append (or bump) a mistake on the role-mistake ledger.

    Returns ``(new_extra, record)`` where ``new_extra`` is a new dict
    the caller persists via the same path the existing audit log uses
    (``workflow_state.update_session``). The original *extra* is not
    mutated.

    Resolution rule for repeats:
      * same ``(role_id, mistake_key)`` → ``occurrence_count += 1``,
        ``last_seen_at`` advances, severity can only escalate.
      * new key → fresh record with ``occurrence_count == 1``.

    The ledger is capped at *max_entries*; oldest-by-``last_seen_at``
    entries fall off so a long-lived session row never grows
    unbounded. Defaults to 64 because preflight judgement only needs
    "what has bitten this role recently", not a full failure history.
    """

    role_id = str(role_id or "").strip()
    mistake_key = str(mistake_key or "").strip()
    if not role_id or not mistake_key:
        raise ValueError("role_id and mistake_key are required")

    when_iso = (when or _utc_now_iso()).strip() or _utc_now_iso()
    severity_norm = _normalise_severity(severity)
    source_norm = _normalise_source_kind(source_kind)

    new_extra: dict = dict(extra or {})
    raw = new_extra.get(SESSION_EXTRA_KEY)
    existing: list[MistakeRecord] = []
    if isinstance(raw, list):
        for item in raw:
            entry = MistakeRecord.from_payload(item)
            if entry is not None:
                existing.append(entry)

    updated: Optional[MistakeRecord] = None
    output: list[MistakeRecord] = []
    for entry in existing:
        if entry.role_id == role_id and entry.mistake_key == mistake_key:
            updated = entry.bump(
                when=when_iso,
                summary=summary,
                prevention_hint=prevention_hint,
                severity=severity_norm,
                source_kind=source_norm,
            )
            output.append(updated)
        else:
            output.append(entry)
    if updated is None:
        updated = MistakeRecord(
            role_id=role_id,
            mistake_key=mistake_key,
            summary=summary.strip(),
            severity=severity_norm,
            prevention_hint=prevention_hint.strip(),
            source_kind=source_norm,
            first_seen_at=when_iso,
            last_seen_at=when_iso,
            occurrence_count=1,
        )
        output.append(updated)

    output.sort(key=lambda r: r.last_seen_at)
    if len(output) > max_entries:
        output = output[-max_entries:]
    new_extra[SESSION_EXTRA_KEY] = [dict(r.to_payload()) for r in output]
    return new_extra, updated


def read_mistake_ledger(source: Any) -> Tuple[MistakeRecord, ...]:
    """Read mistake records out of a session-shaped object or extra dict.

    Returns oldest-first (ordered by ``last_seen_at`` ascending).
    Empty tuple when nothing recorded — never raises.
    """

    if source is None:
        return ()
    if isinstance(source, Mapping):
        extra = source
    else:
        extra = getattr(source, "extra", None)
    if not isinstance(extra, Mapping):
        return ()
    raw = extra.get(SESSION_EXTRA_KEY)
    if not isinstance(raw, list):
        return ()
    out: list[MistakeRecord] = []
    for item in raw:
        entry = MistakeRecord.from_payload(item)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda r: r.last_seen_at)
    return tuple(out)


def mistakes_for_role(source: Any, role_id: str) -> Tuple[MistakeRecord, ...]:
    """Subset of the ledger filtered to a single role."""

    role_id = str(role_id or "").strip()
    if not role_id:
        return ()
    return tuple(
        r for r in read_mistake_ledger(source) if r.role_id == role_id
    )


# ---------------------------------------------------------------------------
# Aggregations + derivations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleMistakeSummary:
    """Per-role projection used by the operator surface."""

    role_id: str
    total_mistakes: int
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int
    total_occurrences: int
    top_recurring: Tuple[MistakeRecord, ...] = ()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "role_id": self.role_id,
            "total_mistakes": self.total_mistakes,
            "high_severity_count": self.high_severity_count,
            "medium_severity_count": self.medium_severity_count,
            "low_severity_count": self.low_severity_count,
            "total_occurrences": self.total_occurrences,
            "top_recurring": [dict(r.to_payload()) for r in self.top_recurring],
        }


def summarize_role_mistakes(
    source: Any,
    *,
    top_recurring: int = 3,
) -> Tuple[RoleMistakeSummary, ...]:
    """Aggregate the ledger into per-role summaries.

    Returned tuple is ordered alphabetically by ``role_id`` so the
    operator surface renders deterministically.
    """

    records = read_mistake_ledger(source)
    grouped: dict[str, list[MistakeRecord]] = {}
    for record in records:
        grouped.setdefault(record.role_id, []).append(record)

    summaries: list[RoleMistakeSummary] = []
    for role_id in sorted(grouped):
        entries = grouped[role_id]
        high = sum(1 for r in entries if r.severity == SEVERITY_HIGH)
        medium = sum(1 for r in entries if r.severity == SEVERITY_MEDIUM)
        low = sum(1 for r in entries if r.severity == SEVERITY_LOW)
        occurrences = sum(r.occurrence_count for r in entries)
        ranked = sorted(
            entries,
            key=lambda r: (
                -r.occurrence_count,
                -_SEVERITY_ORDER[r.severity],
                r.mistake_key,
            ),
        )
        summaries.append(
            RoleMistakeSummary(
                role_id=role_id,
                total_mistakes=len(entries),
                high_severity_count=high,
                medium_severity_count=medium,
                low_severity_count=low,
                total_occurrences=occurrences,
                top_recurring=tuple(ranked[: max(0, int(top_recurring))]),
            )
        )
    return tuple(summaries)


def derive_mistake_from_completion(
    *,
    event: Any,
) -> Optional[Mapping[str, Any]]:
    """Project a :class:`JobCompletionEvent` into mistake-record kwargs.

    Returns ``None`` when the event isn't surface-worthy (success,
    pending approval, or missing role). Otherwise returns the kwargs
    the caller hands to :func:`record_mistake`.

    The producer (e.g. a completion-funnel wrapper) decides *when* to
    persist; this helper only owns the projection so the caller can
    audit-route or no-op without depending on extra-mutation order.
    """

    if event is None:
        return None
    status = str(getattr(event, "status", "") or "").strip().lower()
    if status not in {"blocked", "failed_terminal", "manual"}:
        return None
    role = str(getattr(event, "role", "") or "").strip()
    if not role:
        return None
    reason = str(getattr(event, "reason", "") or "").strip()
    job_type = str(getattr(event, "job_type", "") or "").strip()
    mistake_key = (reason or job_type or "blocked_completion").lower()
    mistake_key = mistake_key.replace(" ", "_")[:64]
    summary = reason or f"{job_type} 작업이 blocked 상태로 종료됨"
    return {
        "role_id": role,
        "mistake_key": mistake_key,
        "summary": summary,
        "prevention_hint": (
            "blocked 사유 점검 후 재진입 전 동일 reason 패턴이 반복되는지 "
            "확인하라."
        ),
        "source_kind": SOURCE_BLOCKED_COMPLETION,
        "severity": SEVERITY_MEDIUM,
    }


def derive_mistake_from_ci_exhaustion(
    *,
    role_id: str,
    failing_runs: Sequence[str],
    pr_number: Optional[int] = None,
    attempts: int = 0,
) -> Optional[Mapping[str, Any]]:
    """Project a CI retry-exhaustion event into mistake-record kwargs.

    Returns ``None`` if *role_id* is empty (the role is the ledger
    primary key — no role, no row).
    """

    role = str(role_id or "").strip()
    if not role:
        return None
    failing = tuple(str(r) for r in failing_runs if r)
    name = failing[0] if failing else "ci_failure"
    mistake_key = f"ci:{name}".lower().replace(" ", "_")[:64]
    pr_part = f" PR #{pr_number}" if pr_number else ""
    summary = (
        f"CI '{name}' 가{pr_part} {attempts}회 재시도 후에도 실패 — "
        "재진입 전 동일 실패 패턴 확인 필요"
    )
    return {
        "role_id": role,
        "mistake_key": mistake_key,
        "summary": summary,
        "prevention_hint": (
            "동일 CI job 이 반복 실패하는 경우 PR diff 와 가장 최근 실패 "
            "로그를 확인한 뒤 변경 후 재실행하라."
        ),
        "source_kind": SOURCE_CI_FAILURE,
        "severity": SEVERITY_HIGH if attempts >= 3 else SEVERITY_MEDIUM,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "MistakeRecord",
    "RoleMistakeSummary",
    "SESSION_EXTRA_KEY",
    "SEVERITIES",
    "SEVERITY_HIGH",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SOURCE_BLOCKED_COMPLETION",
    "SOURCE_CI_FAILURE",
    "SOURCE_KINDS",
    "SOURCE_MANUAL_NOTE",
    "SOURCE_POSTMORTEM",
    "derive_mistake_from_ci_exhaustion",
    "derive_mistake_from_completion",
    "mistakes_for_role",
    "read_mistake_ledger",
    "record_mistake",
    "summarize_role_mistakes",
)
