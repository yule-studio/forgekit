"""Hook-candidate promotion seam — issue #81 round 1.

When a postmortem / blocked completion / CI retry exhaustion produces
a recurring failure reason, a follow-up engineer should be able to
ask "should this become a future blocking hook?" without re-deriving
the answer by hand. This module owns the deterministic projection:

  recurring mistake → :class:`HookCandidate`

Round 1 stops at *contract* — the helper does NOT yet generate the
markdown spec under ``agents/engineering-agent/hooks/``. That live
wiring lands in a follow-up. The deterministic ``future_hook_id`` is
the contract follow-up wiring depends on, so producers can reference
the candidate now and the live writer can pick up the same id when it
lands.

ID shape:

  ``preflight-<role-id>-<mistake-key-slug>``

Examples:
  * role=devops, key=ci:protected_branch_blocked →
    ``preflight-devops-ci-protected-branch-blocked``
  * role=qa-engineer, key=missing_regression_check →
    ``preflight-qa-engineer-missing-regression-check``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .mistake_ledger import (
    MistakeRecord,
    SEVERITY_MEDIUM,
    SOURCE_POSTMORTEM,
)


HOOK_CANDIDATE_ID_PREFIX: str = "preflight"


# The minimum evidence we want before nominating a recurring mistake
# as a hook candidate. Single occurrences are surfaced through the
# normal preflight advisory; we only suggest *a hook* once a pattern
# has bitten more than once.
DEFAULT_MIN_EVIDENCE: int = 2


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookCandidate:
    """Deterministic projection of a recurring mistake into a future hook.

    ``future_hook_id`` is stable across runs given the same
    ``(role_id, mistake_key)`` so the live wiring can recognise an
    already-promoted candidate and avoid duplicate hook spec files.

    ``source_kind`` mirrors the originating mistake row so an audit
    reader can follow the candidate back to its evidence without
    re-querying the ledger.
    """

    future_hook_id: str
    role_id: str
    mistake_key: str
    summary: str
    prevention_hint: str
    source_kind: str
    severity: str
    evidence_count: int

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "future_hook_id": self.future_hook_id,
            "role_id": self.role_id,
            "mistake_key": self.mistake_key,
            "summary": self.summary,
            "prevention_hint": self.prevention_hint,
            "source_kind": self.source_kind,
            "severity": self.severity,
            "evidence_count": self.evidence_count,
        }


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def slugify_hook_id(role_id: str, mistake_key: str) -> str:
    """Derive the deterministic ``future_hook_id`` for *(role, key)*.

    Idempotent: two callers with the same inputs always produce the
    same id. The output is kebab-case ASCII lowercase so it is safe
    to use as a markdown filename or HTTP path segment.
    """

    role = _slug(role_id)
    key = _slug(mistake_key)
    if not role:
        role = "role"
    if not key:
        key = "mistake"
    return f"{HOOK_CANDIDATE_ID_PREFIX}-{role}-{key}"


def promote_record_to_hook_candidate(
    record: MistakeRecord,
    *,
    min_evidence: int = DEFAULT_MIN_EVIDENCE,
) -> Optional[HookCandidate]:
    """Promote a :class:`MistakeRecord` to a hook candidate.

    Returns ``None`` when the record doesn't meet the evidence bar so
    the caller can iterate over the entire ledger and filter
    candidates in one pass.
    """

    if record.occurrence_count < max(1, int(min_evidence)):
        return None
    return HookCandidate(
        future_hook_id=slugify_hook_id(record.role_id, record.mistake_key),
        role_id=record.role_id,
        mistake_key=record.mistake_key,
        summary=record.summary,
        prevention_hint=record.prevention_hint,
        source_kind=record.source_kind,
        severity=record.severity,
        evidence_count=record.occurrence_count,
    )


def promote_postmortem_to_hook_candidate(
    *,
    role_id: str,
    mistake_key: str,
    summary: str,
    prevention_hint: str,
    severity: str = SEVERITY_MEDIUM,
    evidence_count: int = 1,
    source_kind: str = SOURCE_POSTMORTEM,
) -> HookCandidate:
    """Build a hook candidate directly from postmortem inputs.

    Used by the failure-postmortem producer and the CI retry
    exhaustion path. Unlike :func:`promote_record_to_hook_candidate`
    this never returns ``None`` — the caller has decided the postmortem
    *is* worth promoting; this helper just stamps the deterministic id.
    """

    role = str(role_id or "").strip()
    key = str(mistake_key or "").strip()
    if not role or not key:
        raise ValueError("role_id and mistake_key are required")
    return HookCandidate(
        future_hook_id=slugify_hook_id(role, key),
        role_id=role,
        mistake_key=key,
        summary=summary.strip(),
        prevention_hint=prevention_hint.strip(),
        source_kind=source_kind,
        severity=severity,
        evidence_count=max(1, int(evidence_count)),
    )


def collect_hook_candidates(
    records: Iterable[MistakeRecord],
    *,
    min_evidence: int = DEFAULT_MIN_EVIDENCE,
) -> Tuple[HookCandidate, ...]:
    """Filter the ledger down to its hook-candidate-worthy rows.

    Returned tuple is sorted by ``evidence_count`` descending then by
    ``future_hook_id`` so the operator surface always shows the
    strongest candidates at the top.
    """

    out: list[HookCandidate] = []
    for record in records:
        candidate = promote_record_to_hook_candidate(
            record, min_evidence=min_evidence
        )
        if candidate is not None:
            out.append(candidate)
    out.sort(
        key=lambda c: (-c.evidence_count, c.future_hook_id)
    )
    return tuple(out)


def render_hook_candidate_block(
    candidates: Sequence[HookCandidate],
) -> str:
    """One-line-per-candidate operator-readable block.

    Returns the empty string when *candidates* is empty so the caller
    can append unconditionally without growing the surface.
    """

    if not candidates:
        return ""
    lines: list[str] = ["hook 후보:"]
    for candidate in candidates:
        lines.append(
            f"  - `{candidate.future_hook_id}` "
            f"role=`{candidate.role_id}` "
            f"({candidate.evidence_count}회, {candidate.severity}) "
            f"— {candidate.summary}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SLUG_RE.sub("-", text)
    return text.strip("-")


__all__ = (
    "DEFAULT_MIN_EVIDENCE",
    "HOOK_CANDIDATE_ID_PREFIX",
    "HookCandidate",
    "collect_hook_candidates",
    "promote_postmortem_to_hook_candidate",
    "promote_record_to_hook_candidate",
    "render_hook_candidate_block",
    "slugify_hook_id",
)
