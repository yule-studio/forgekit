"""Internal approval levels (repo-autopilot WT1) — "user 승인 없음" ≠ "internal 승인 없음".

repo-autopilot can run without the USER approving each step, but it can NEVER run
without the INTERNAL chain (PM → gateway → tech-lead) approving. The levels make that
explicit:

* ``L0_collect``        — observe / scan. No approval needed (read-only).
* ``L1_propose``        — produce a packet / proposal. Internal, still no execution.
* ``L2_internal_approve`` — tech-lead signoff for a SAFE-class change. This is the
  bar autopilot can clear on its own (internal approval, no user) → executable.
* ``L3_user_approve``   — risky change: needs the USER. Autopilot stops at propose.
* ``L4_restricted``     — deploy / secret / infra / destructive: never auto, operator
  only (runbook).

So a safe change at L2 is "executable without the user, but only after PM→gateway→
tech-lead". Pure mapping → testable.
"""

from __future__ import annotations

from typing import Tuple

L0_COLLECT = "L0_collect"
L1_PROPOSE = "L1_propose"
L2_INTERNAL_APPROVE = "L2_internal_approve"
L3_USER_APPROVE = "L3_user_approve"
L4_RESTRICTED = "L4_restricted"

ALL_LEVELS: Tuple[str, ...] = (L0_COLLECT, L1_PROPOSE, L2_INTERNAL_APPROVE,
                               L3_USER_APPROVE, L4_RESTRICTED)

# the only level autopilot may EXECUTE on its own (internal-approved safe class).
_EXECUTABLE_INTERNAL = frozenset({L2_INTERNAL_APPROVE})

# wording → restricted (never auto)
_RESTRICTED = ("deploy", "배포", "secret", "비밀", "infra", "인프라", "iam",
               "migration", "마이그레이션", "production", "프로덕션", "destructive", "rm -rf")
# wording → risky (needs the user)
_RISKY = ("rewrite", "대규모", "broad", "schema", "auth", "권한", "삭제", "delete")


def classify_level(text: str, *, risk_class: str = "") -> str:
    """Classify a unit of work → an approval level (restricted > user > internal)."""

    blob = f"{text} {risk_class}".lower()
    if risk_class == "blocked" or any(k in blob for k in _RESTRICTED):
        return L4_RESTRICTED
    if risk_class == "risky" or any(k in blob for k in _RISKY):
        return L3_USER_APPROVE
    # safe class (docs/tests/lint/small refactor) — internal approval suffices
    return L2_INTERNAL_APPROVE


def autopilot_can_execute(level: str) -> bool:
    """True ONLY for an internal-approved safe class (L2). L3/L4 → needs user/operator."""

    return level in _EXECUTABLE_INTERNAL


def needs_user(level: str) -> bool:
    return level in (L3_USER_APPROVE, L4_RESTRICTED)


__all__ = (
    "L0_COLLECT", "L1_PROPOSE", "L2_INTERNAL_APPROVE", "L3_USER_APPROVE", "L4_RESTRICTED",
    "ALL_LEVELS", "classify_level", "autopilot_can_execute", "needs_user",
)
