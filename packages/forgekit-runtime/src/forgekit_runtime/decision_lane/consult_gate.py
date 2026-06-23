"""Consult-required merge gate — "design/review 가 필요한데 consult 없이 머지" 차단.

The decision-lane already has the consult *artifact* (:class:`ConsultNote` +
:func:`validate_consult`): a real, attributable "one role asked another before fixing a
decision" trace. What it did **not** have was the *merge-time question*: for a given
lane/PR, **was a consult required at all**, and if so, **is the artifact present** (a
valid consult verdict, a design/decision-log reference, or a recorded waive reason)?

This module is that gate. It is the integration-wave merge criterion:

* **consult required + artifact missing → merge 금지** (``blocker=True``);
* **consult required + artifact present (valid consult / design-log ref / waive) → 통과 가능**;
* **consult not required → 통과 가능**.

Pure — no I/O. Adjudicates a described change; the QA report / merge-prep checklist render
the verdict. Consult *content* validity is delegated to :func:`validate_consult` (so a
fake "we consulted X" with no consultee or no question does **not** satisfy the gate).

Docs SSoT: ``docs/pm-techlead-lane.md`` (consult artifact) + the wave QA report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from .schemas import ConsultNote
from .validators import validate_consult

# --- gate verdict states -----------------------------------------------------
CONSULT_NOT_REQUIRED = "not_required"   # change carries no design/review surface → pass
CONSULT_SATISFIED = "satisfied"          # required + a real artifact present → pass
CONSULT_WAIVED = "waived"                # required + an explicit, recorded waive reason → pass
CONSULT_MISSING = "missing"              # required + no artifact / no waive → BLOCKER
GATE_STATES: Tuple[str, ...] = (
    CONSULT_NOT_REQUIRED, CONSULT_SATISFIED, CONSULT_WAIVED, CONSULT_MISSING,
)

# Change kinds that REQUIRE a consult before merge — they encode a design/review
# decision that another role should have had a chance to weigh in on. Vendor-neutral,
# matched case-insensitively against a change's declared kinds.
CONSULT_REQUIRING_KINDS: Tuple[str, ...] = (
    "design", "architecture", "stack", "api-contract", "schema", "data-model",
    "security", "ux", "design-system", "public-interface", "dependency-policy",
)

# Change kinds that, on their own, never require a consult (pure verification /
# documentation / mechanical work). A change is consult-required iff it declares at
# least one CONSULT_REQUIRING_KIND — these are listed only for documentation/clarity.
NON_DESIGN_KINDS: Tuple[str, ...] = (
    "docs", "test", "qa", "integration", "evidence", "chore", "ci", "refactor-internal",
)


@dataclass(frozen=True)
class ChangeUnderReview:
    """One lane/PR up for merge, plus whatever consult evidence it carries.

    ``change_kinds`` is what the change *does* (design / api-contract / docs / test …).
    The gate decides "required?" from these. The three evidence fields are the only ways
    to satisfy a required consult — anything else is a missing artifact (blocker)."""

    ref: str                                     # PR# / lane id / branch
    summary: str = ""
    change_kinds: Tuple[str, ...] = ()
    consult: Optional[ConsultNote] = None        # a recorded consult verdict
    design_refs: Tuple[str, ...] = ()            # design/decision-log artifact ids (근거)
    waive_reason: str = ""                        # explicit, recorded reason to skip consult

    def to_dict(self) -> dict:
        return {"ref": self.ref, "summary": self.summary,
                "change_kinds": list(self.change_kinds),
                "consult": self.consult.to_dict() if self.consult else None,
                "design_refs": list(self.design_refs), "waive_reason": self.waive_reason}


@dataclass(frozen=True)
class ConsultGateVerdict:
    """Merge-gate verdict for one change. ``blocker`` is True **only** when a consult was
    required and no satisfying artifact exists — that and only that blocks merge."""

    ref: str
    required: bool
    status: str                                  # one of GATE_STATES
    blocker: bool
    artifact: str = ""                           # "consult:<id>" / "design-log:<ref>" / "waived:<reason>"
    reasons: Tuple[str, ...] = ()

    def merge_ok(self) -> bool:
        """A change may merge unless its consult is required-and-missing."""
        return not self.blocker

    def to_dict(self) -> dict:
        return {"ref": self.ref, "required": self.required, "status": self.status,
                "blocker": self.blocker, "artifact": self.artifact,
                "reasons": list(self.reasons), "merge_ok": self.merge_ok()}

    def line(self) -> str:
        mark = {CONSULT_SATISFIED: "✓", CONSULT_WAIVED: "⊘",
                CONSULT_MISSING: "✗", CONSULT_NOT_REQUIRED: "·"}.get(self.status, "?")
        tail = f" [{self.artifact}]" if self.artifact else ""
        req = "required" if self.required else "not-required"
        return f"  {mark} {self.ref} — consult {req} → {self.status}{tail}"


def consult_required(change: ChangeUnderReview) -> bool:
    """True iff the change declares at least one design/review-bearing kind."""
    kinds = {k.strip().lower() for k in change.change_kinds if k and k.strip()}
    return bool(kinds & set(CONSULT_REQUIRING_KINDS))


def _blank(s: str) -> bool:
    return not s or not s.strip()


def adjudicate_consult(change: ChangeUnderReview) -> ConsultGateVerdict:
    """Re-judge one change's consult requirement and verify the artifact.

    required + valid consult       → satisfied
    required + design/decision ref → satisfied (the decision already carries the rationale)
    required + recorded waive      → waived (explicit, attributable skip)
    required + none of the above   → missing (blocker — merge 금지)
    not required                   → not_required (pass)
    """

    if not consult_required(change):
        return ConsultGateVerdict(
            change.ref, required=False, status=CONSULT_NOT_REQUIRED, blocker=False,
            reasons=("design/review surface 없음 — consult 불필요",))

    # 1. a real consult verdict (content validated — fake claim does NOT satisfy).
    if change.consult is not None:
        viol = validate_consult(change.consult)
        if not viol:
            return ConsultGateVerdict(
                change.ref, required=True, status=CONSULT_SATISFIED, blocker=False,
                artifact=f"consult:{change.consult.consult_id}",
                reasons=(f"consult {change.consult.consult_id}: "
                         f"{change.consult.by_role}→{list(change.consult.to_roles)}",))
        # a present-but-fake consult is NOT a pass — fall through, but say why.
        fake_reasons = (("consult 제출됐으나 무효:",) + viol)
    else:
        fake_reasons = ()

    # 2. a design/decision-log reference carrying the rationale.
    refs = tuple(r for r in change.design_refs if r and r.strip())
    if refs:
        return ConsultGateVerdict(
            change.ref, required=True, status=CONSULT_SATISFIED, blocker=False,
            artifact=f"design-log:{refs[0]}",
            reasons=(f"design/decision 근거: {list(refs)}",) + fake_reasons)

    # 3. an explicit, recorded waive reason.
    if not _blank(change.waive_reason):
        return ConsultGateVerdict(
            change.ref, required=True, status=CONSULT_WAIVED, blocker=False,
            artifact=f"waived:{change.waive_reason.strip()}",
            reasons=(f"consult waived — 사유: {change.waive_reason.strip()}",) + fake_reasons)

    # 4. nothing — required but missing → blocker.
    base = ("consult required 인데 consult verdict / design·decision 근거 / waive 사유 "
            "모두 없음 — merge 금지",)
    return ConsultGateVerdict(
        change.ref, required=True, status=CONSULT_MISSING, blocker=True,
        reasons=base + fake_reasons)


@dataclass(frozen=True)
class ConsultGateReport:
    """Wave-level roll-up the QA report renders: satisfied / waived / missing(blocker) /
    not-required, split, plus the single merge-blocked answer."""

    verdicts: Tuple[ConsultGateVerdict, ...] = ()

    @property
    def satisfied(self) -> Tuple[ConsultGateVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status == CONSULT_SATISFIED)

    @property
    def waived(self) -> Tuple[ConsultGateVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status == CONSULT_WAIVED)

    @property
    def missing(self) -> Tuple[ConsultGateVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status == CONSULT_MISSING)

    @property
    def not_required(self) -> Tuple[ConsultGateVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status == CONSULT_NOT_REQUIRED)

    @property
    def merge_blocked(self) -> bool:
        """The whole wave is merge-blocked if any change is required-and-missing."""
        return any(v.blocker for v in self.verdicts)

    def to_dict(self) -> dict:
        return {"satisfied": [v.ref for v in self.satisfied],
                "waived": [v.ref for v in self.waived],
                "missing": [v.ref for v in self.missing],
                "not_required": [v.ref for v in self.not_required],
                "merge_blocked": self.merge_blocked,
                "verdicts": [v.to_dict() for v in self.verdicts]}

    def lines(self) -> Tuple[str, ...]:
        out = [f"consult merge gate — {len(self.verdicts)} change, "
               + ("MERGE BLOCKED" if self.merge_blocked else "통과 가능")]
        out.append(f"  satisfied={len(self.satisfied)} waived={len(self.waived)} "
                   f"missing={len(self.missing)} not-required={len(self.not_required)}")
        for v in self.verdicts:
            out.append(v.line())
        return tuple(out)


def consult_gate_report(changes: Sequence[ChangeUnderReview]) -> ConsultGateReport:
    """Adjudicate every change and roll up into the wave merge verdict."""
    return ConsultGateReport(tuple(adjudicate_consult(c) for c in changes))


__all__ = (
    "CONSULT_NOT_REQUIRED", "CONSULT_SATISFIED", "CONSULT_WAIVED", "CONSULT_MISSING",
    "GATE_STATES", "CONSULT_REQUIRING_KINDS", "NON_DESIGN_KINDS",
    "ChangeUnderReview", "ConsultGateVerdict", "ConsultGateReport",
    "consult_required", "adjudicate_consult", "consult_gate_report",
)
