"""Activation lifecycle — the state model for an external tool/skill/plugin candidate.

The governance question this lane answers is **"추천됨" ≠ "설치됨" ≠ "실행됨"**: a
candidate surfaced by discovery is NOT the same as a tool the runtime installed, which is
NOT the same as a tool it actually ran. Collapsing those into one boolean ("installed?")
is exactly how supply-chain risk slips in. So a candidate carries an explicit lifecycle
state, and the ONLY way to reach an active state (enabled/executed) is through the
approval chain (:mod:`.bridge`).

States (pre-activation lane → terminal):

* ``collected``          — surfaced by discovery, raw, unvetted.
* ``curated``            — vetted / authored into a curated note (a human looked at it).
* ``armory-registered``  — has a catalog :class:`armory.WeaponSpec` (a known capability).
* ``attachable``         — present + verified + safe → usable WITHOUT install (no risk).
* ``install-required``   — not present → activation needs an install (supply-chain risk).
* ``approval-needed``    — install/enable is risky/global-write → the gate must approve.
* ``enabled``            — approved AND activated (installed/attached) — proven, not faked.
* ``executed``           — actually ran under an execution receipt.
* ``blocked``            — refused (destructive, or risky without approval).

``collected → curated → armory-registered`` is the **recommendation** track; an operator
or audit can read a candidate in any of those states and know it is NOT installed.
``attachable``/``install-required``/``approval-needed`` are the **readiness** states the
classifier derives from the candidate's facts. ``enabled``/``executed``/``blocked`` are
the **outcome** states only the bridge may set (after a real verdict).

Pure / stdlib-only — no IO, no approval logic here (that is the bridge's job).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Mapping, Tuple

# --- lifecycle states ---------------------------------------------------------
ST_COLLECTED = "collected"
ST_CURATED = "curated"
ST_ARMORY_REGISTERED = "armory-registered"
ST_ATTACHABLE = "attachable"
ST_INSTALL_REQUIRED = "install-required"
ST_APPROVAL_NEEDED = "approval-needed"
ST_ENABLED = "enabled"
ST_EXECUTED = "executed"
ST_BLOCKED = "blocked"

ALL_STATES: Tuple[str, ...] = (
    ST_COLLECTED, ST_CURATED, ST_ARMORY_REGISTERED, ST_ATTACHABLE,
    ST_INSTALL_REQUIRED, ST_APPROVAL_NEEDED, ST_ENABLED, ST_EXECUTED, ST_BLOCKED,
)

# the "recommended, NOT installed" track — reading any of these means: not active.
RECOMMENDATION_STATES: FrozenSet[str] = frozenset(
    {ST_COLLECTED, ST_CURATED, ST_ARMORY_REGISTERED})
# readiness states the classifier derives (still NOT active).
READINESS_STATES: FrozenSet[str] = frozenset(
    {ST_ATTACHABLE, ST_INSTALL_REQUIRED, ST_APPROVAL_NEEDED})
# outcome states only the bridge sets, after a real verdict.
OUTCOME_STATES: FrozenSet[str] = frozenset({ST_ENABLED, ST_EXECUTED, ST_BLOCKED})
# terminal (no forward transition out).
TERMINAL_STATES: FrozenSet[str] = frozenset({ST_EXECUTED, ST_BLOCKED})
# "active" = the runtime has actually attached/run it (the supply-chain-relevant set).
ACTIVE_STATES: FrozenSet[str] = frozenset({ST_ENABLED, ST_EXECUTED})

# forward transition map — every legal advance. Notably:
# * nothing reaches enabled/executed EXCEPT through approval-needed or attachable, and
# * blocked is reachable from any pre-outcome state (the gate can always refuse).
_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    ST_COLLECTED: frozenset({ST_CURATED, ST_BLOCKED}),
    ST_CURATED: frozenset({ST_ARMORY_REGISTERED, ST_BLOCKED}),
    ST_ARMORY_REGISTERED: frozenset(
        {ST_ATTACHABLE, ST_INSTALL_REQUIRED, ST_APPROVAL_NEEDED, ST_BLOCKED}),
    ST_ATTACHABLE: frozenset({ST_APPROVAL_NEEDED, ST_ENABLED, ST_BLOCKED}),
    ST_INSTALL_REQUIRED: frozenset({ST_APPROVAL_NEEDED, ST_BLOCKED}),
    ST_APPROVAL_NEEDED: frozenset({ST_ENABLED, ST_BLOCKED}),
    ST_ENABLED: frozenset({ST_EXECUTED, ST_BLOCKED}),
    ST_EXECUTED: frozenset(),
    ST_BLOCKED: frozenset(),
}


def can_transition(frm: str, to: str) -> bool:
    """True iff *frm* → *to* is a legal lifecycle advance."""

    return to in _TRANSITIONS.get(frm, frozenset())


def next_states(frm: str) -> Tuple[str, ...]:
    """The states reachable in one legal step from *frm* (sorted, stable)."""

    return tuple(sorted(_TRANSITIONS.get(frm, frozenset())))


@dataclass(frozen=True)
class ActivationCandidate:
    """An external tool/skill/plugin the runtime is considering activating + its facts.

    The classifier reads the FACTS (source/present/needs_install/global_write/safety) to
    derive a readiness state and a risk class; the lifecycle ``state`` is where the
    candidate is *now*. ``evidence``/``why`` answer the audit question the operator will
    ask later — "왜 이 도구를 썼는가".
    """

    id: str
    kind: str = "tool"                 # tool / skill / plugin
    display_name: str = ""
    source: str = "external"           # builtin / armory / discovery / external
    state: str = ST_COLLECTED
    present: bool = False              # already on the system (verified)
    needs_install: bool = False        # activation requires an install step
    global_write: bool = False         # activation writes outside the repo / global config
    safety: str = ""                   # "safe" / "risky" / "" (unknown → conservative)
    armory_registered: bool = False    # a catalog WeaponSpec exists
    curated: bool = False              # vetted / authored into a curated note
    verify_command: str = ""           # how presence is/should be checked
    why: str = ""                      # operator rationale — the audit answer

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "display_name": self.display_name,
            "source": self.source, "state": self.state, "present": self.present,
            "needs_install": self.needs_install, "global_write": self.global_write,
            "safety": self.safety, "armory_registered": self.armory_registered,
            "curated": self.curated, "verify_command": self.verify_command, "why": self.why,
        }


def derive_readiness_state(cand: ActivationCandidate) -> str:
    """Map a candidate's FACTS → the lifecycle state it should be in *before* the gate.

    This is the recommendation→readiness routing (NEVER an outcome state — only the
    bridge sets enabled/executed/blocked after a verdict):

    * not curated                → ``collected``  (raw, unvetted)
    * curated, no armory spec     → ``curated``
    * armory-registered, but needs install / global-write / risky-or-unknown safety
                                  → ``install-required`` (install) or ``approval-needed``
    * armory-registered + present + safe + no global write
                                  → ``attachable`` (usable without install — the only
                                    pre-gate state that can go straight to enabled)
    """

    if not cand.curated:
        return ST_COLLECTED
    if not cand.armory_registered:
        return ST_CURATED
    # armory-registered from here on.
    risky_safety = cand.safety != "safe"   # "risky" or unknown → conservative
    if cand.needs_install or not cand.present:
        # an install step is required → at minimum install-required; if it also writes
        # globally or the tool's safety is not proven, it needs explicit approval.
        if cand.global_write or risky_safety:
            return ST_APPROVAL_NEEDED
        return ST_INSTALL_REQUIRED
    # present, no install needed.
    if cand.global_write or risky_safety:
        return ST_APPROVAL_NEEDED
    return ST_ATTACHABLE


__all__ = (
    "ST_COLLECTED", "ST_CURATED", "ST_ARMORY_REGISTERED", "ST_ATTACHABLE",
    "ST_INSTALL_REQUIRED", "ST_APPROVAL_NEEDED", "ST_ENABLED", "ST_EXECUTED", "ST_BLOCKED",
    "ALL_STATES", "RECOMMENDATION_STATES", "READINESS_STATES", "OUTCOME_STATES",
    "TERMINAL_STATES", "ACTIVE_STATES",
    "can_transition", "next_states", "ActivationCandidate", "derive_readiness_state",
)
