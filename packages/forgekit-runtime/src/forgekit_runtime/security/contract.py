"""Red/blue security contracts (WT5) — own-assets-only, plan-first, approval-gated.

Hard safety rails baked into the types: a drill targets ONLY an allowlisted, isolated
own asset (own server / own k3s namespace / localhost); it is **dry-run / plan-only by
default**; an *active* drill needs explicit operator approval; public-internet / third-
party targets are structurally rejected. Offensive tooling is never produced — only a
PLAN + a DefenseRunbook. Pure dataclasses → testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

# target kinds — all must be OWN, ISOLATED assets ----------------------------
TARGET_OWN_SERVER = "own-server"
TARGET_K3S_NAMESPACE = "k3s-namespace"
TARGET_LOCALHOST = "localhost"
_OWN_KINDS = frozenset({TARGET_OWN_SERVER, TARGET_K3S_NAMESPACE, TARGET_LOCALHOST})

# drill status ----------------------------------------------------------------
DRILL_PLAN_ONLY = "plan-only"      # default — nothing runs
DRILL_BLOCKED = "blocked"          # target not allowed → refused
DRILL_APPROVED_ACTIVE = "approved-active"  # operator approved an active drill


@dataclass(frozen=True)
class TargetSpec:
    """A drill target. Only allowlisted + isolated OWN assets are eligible."""

    id: str
    kind: str
    allowlisted: bool = False
    isolated: bool = True            # isolated env (e.g. dedicated k3s namespace)
    note: str = ""

    @property
    def eligible(self) -> bool:
        # own-kind + allowlisted + isolated. Anything else (public/3rd-party) → False.
        return self.kind in _OWN_KINDS and self.allowlisted and self.isolated

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "allowlisted": self.allowlisted,
                "isolated": self.isolated, "eligible": self.eligible, "note": self.note}


@dataclass(frozen=True)
class AttackPlan:
    """A PLAN (not execution): hypotheses + checks against an own asset. dry_run always
    starts True; only an approved active drill flips it."""

    target_id: str
    hypotheses: Tuple[str, ...] = ()
    checks: Tuple[str, ...] = ()
    dry_run: bool = True

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "hypotheses": list(self.hypotheses),
                "checks": list(self.checks), "dry_run": self.dry_run}


@dataclass(frozen=True)
class DefenseRunbook:
    """Blue-side hardening / detection / mitigation steps (the useful output)."""

    target_id: str
    hardening: Tuple[str, ...] = ()
    detection: Tuple[str, ...] = ()
    mitigation: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "hardening": list(self.hardening),
                "detection": list(self.detection), "mitigation": list(self.mitigation)}


@dataclass(frozen=True)
class FindingsDigest:
    target_id: str
    findings: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "findings": list(self.findings)}


@dataclass(frozen=True)
class SecurityDrillPacket:
    """The drill artifact — plan + defense runbook + status. Never auto-executes."""

    target: TargetSpec
    attack_plan: AttackPlan
    defense_runbook: DefenseRunbook
    status: str = DRILL_PLAN_ONLY
    requires_approval: bool = True
    refusal_reason: str = ""

    @property
    def executed(self) -> bool:
        return self.status == DRILL_APPROVED_ACTIVE and not self.attack_plan.dry_run

    def to_dict(self) -> dict:
        return {
            "target": self.target.to_dict(), "attack_plan": self.attack_plan.to_dict(),
            "defense_runbook": self.defense_runbook.to_dict(), "status": self.status,
            "requires_approval": self.requires_approval, "refusal_reason": self.refusal_reason,
        }


__all__ = (
    "TARGET_OWN_SERVER", "TARGET_K3S_NAMESPACE", "TARGET_LOCALHOST",
    "DRILL_PLAN_ONLY", "DRILL_BLOCKED", "DRILL_APPROVED_ACTIVE",
    "TargetSpec", "AttackPlan", "DefenseRunbook", "FindingsDigest", "SecurityDrillPacket",
)
