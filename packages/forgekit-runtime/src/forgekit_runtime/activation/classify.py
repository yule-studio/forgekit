"""Activation safety classification — attach / install / enable / execute → safe/risky/blocked.

The forge classifier asks "is this forged work safe to run?". This one asks the
**supply-chain** question: "is *activating this external capability* safe?". The two are
siblings (both derive an autopilot approval level), but activating an external tool has
its own risk surface the forge plan doesn't:

* **install-required** — putting new code on the machine is never a safe-class act.
* **global write** — touching PATH / global config / outside the repo escalates risk.
* **external / unknown source** — a candidate that is not builtin and not in the vetted
  armory is, by provenance, untrusted (supply chain).
* **unknown safety** — a tool with no declared safety class is treated as risky, never
  silently safe (safe-by-rejection, mirroring the forge weapon resolver).

The STRICTEST of these wins. The only path to ``safe`` is an *attach* of a present,
armory-registered, safe-safety, no-global-write tool — everything else is risky or
blocked. Destructive wording (deploy/secret/rm -rf) or an explicitly forbidden candidate
→ ``blocked`` (never auto-runs). Pure + injectable (the action is data, not IO).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..autopilot import approval as A
from .states import ActivationCandidate

# disposition vocabulary the user asked for: safe / risky / blocked.
SAFE = "safe"
RISKY = "risky"
BLOCKED = "blocked"

# activation actions
ACT_ATTACH = "attach"      # use an already-present tool (no install)
ACT_INSTALL = "install"    # put the tool on the machine
ACT_ENABLE = "enable"      # turn a present/installed tool on
ACT_EXECUTE = "execute"    # actually run it
ACTIONS: Tuple[str, ...] = (ACT_ATTACH, ACT_INSTALL, ACT_ENABLE, ACT_EXECUTE)

_LEVEL_ORDER = {A.L2_INTERNAL_APPROVE: 0, A.L3_USER_APPROVE: 1, A.L4_RESTRICTED: 2}
_CLASS_BY_LEVEL = {A.L2_INTERNAL_APPROVE: SAFE, A.L3_USER_APPROVE: RISKY,
                   A.L4_RESTRICTED: BLOCKED}

# sources the runtime trusts by provenance (everything else is supply-chain-untrusted).
_TRUSTED_SOURCES = frozenset({"builtin", "armory"})


@dataclass(frozen=True)
class ActivationClassification:
    """The derived risk of activating a candidate + WHY (explainable, evidence-able)."""

    action: str
    disposition: str                   # safe / risky / blocked
    approval_level: str                # autopilot.approval L*
    supply_chain_flags: Tuple[str, ...] = ()   # install_required / global_write / ...
    reasons: Tuple[str, ...] = ()

    @property
    def needs_approval(self) -> bool:
        """True when a human/operator approval is required before activation."""

        return self.disposition in (RISKY, BLOCKED)

    def to_dict(self) -> dict:
        return {"action": self.action, "disposition": self.disposition,
                "approval_level": self.approval_level,
                "supply_chain_flags": list(self.supply_chain_flags),
                "reasons": list(self.reasons)}


def _max_level(a: str, b: str) -> str:
    return a if _LEVEL_ORDER.get(a, 1) >= _LEVEL_ORDER.get(b, 1) else b


def classify_activation(
    cand: ActivationCandidate,
    action: str,
    *,
    forbidden: bool = False,
) -> ActivationClassification:
    """Derive the strictest activation risk for *cand* under *action*.

    ``forbidden=True`` (a denylisted candidate, or destructive intent the caller already
    knows) hard-blocks. Otherwise the level is the max of: the action's base risk, the
    supply-chain flags, and the candidate id/name wording (deploy/secret → restricted).
    """

    reasons = []
    flags = []

    if action not in ACTIONS:
        return ActivationClassification(
            action=action, disposition=BLOCKED, approval_level=A.L4_RESTRICTED,
            reasons=(f"알 수 없는 activation action: {action!r}",))

    # Risk lives in the candidate's FACTS (new/external/unverified code), NOT the verb —
    # executing an already-present, vetted, safe armory tool is the no-risk happy path.
    # The base level is internal-safe; the supply-chain flags below escalate it. The one
    # verb that carries inherent risk is ``install`` (it puts new code on the machine).
    level = A.L2_INTERNAL_APPROVE

    # supply-chain flags (each can only escalate).
    if cand.needs_install or (action == ACT_INSTALL):
        flags.append("install_required")
        reasons.append("설치 필요 — 새 코드를 머신에 올림")
        level = _max_level(level, A.L3_USER_APPROVE)
    if cand.global_write:
        flags.append("global_write")
        reasons.append("global write — PATH/전역 설정/repo 외부 변경")
        level = _max_level(level, A.L3_USER_APPROVE)
    if cand.source not in _TRUSTED_SOURCES:
        flags.append("external_source")
        reasons.append(f"미신뢰 출처({cand.source}) — 공급망 리스크")
        level = _max_level(level, A.L3_USER_APPROVE)
    if cand.safety != "safe":
        flags.append("unknown_safety" if not cand.safety else "declared_risky")
        reasons.append("safety 미증명(보수적 risky)" if not cand.safety
                       else "armory safety=risky")
        level = _max_level(level, A.L3_USER_APPROVE)

    # wording ladder — a candidate that names deploy/secret/infra is restricted.
    blob = f"{cand.id} {cand.display_name} {cand.why}"
    text_level = A.classify_level(blob)
    if _LEVEL_ORDER.get(text_level, 0) > _LEVEL_ORDER.get(level, 0):
        reasons.append(f"wording → {text_level}")
    level = _max_level(level, text_level)

    if forbidden:
        reasons.append("forbidden — 명시적 차단 대상")
        level = A.L4_RESTRICTED

    return ActivationClassification(
        action=action, disposition=_CLASS_BY_LEVEL.get(level, RISKY),
        approval_level=level, supply_chain_flags=tuple(flags), reasons=tuple(reasons))


__all__ = (
    "SAFE", "RISKY", "BLOCKED",
    "ACT_ATTACH", "ACT_INSTALL", "ACT_ENABLE", "ACT_EXECUTE", "ACTIONS",
    "ActivationClassification", "classify_activation",
)
