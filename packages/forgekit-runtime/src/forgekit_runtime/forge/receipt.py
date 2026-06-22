"""Forge execution receipt — proof that a forged work packet ran under a real approval.

The harness already has a per-run :class:`ExecutionReceipt` (context/grants/runner). This
is the GOVERNANCE receipt for the **forge → execution** path: it binds one Hephaistos
forge plan to the approval chain that authorized it. A receipt is the artifact an operator
(or an audit) reads to answer "who was equipped, to do what, classified how, approved by
whom, and what did the commit carry".

Anti-fake is the whole point (:func:`validate_forge_receipt`):

* a receipt may be ``authorized`` ONLY with real approval metadata, a registry-known
  executor, and commit trailers (a blocked verdict produces NO trailers);
* ``outcome == "executed"`` REQUIRES ``authorized`` (no fabricated execution);
* a non-authorized receipt MUST carry blocking reasons and MUST NOT carry trailers or an
  executed outcome.

So there is no way to mint a receipt that claims approval/execution that did not happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from forgekit_config.identity.registry import is_known

# outcome states (mirror selfimprove.execute_bridge — honest)
OUTCOME_EXECUTED = "executed"
OUTCOME_BLOCKED = "blocked"
OUTCOME_AWAITING = "awaiting"
OUTCOME_ERROR = "error"
OUTCOMES: Tuple[str, ...] = (OUTCOME_EXECUTED, OUTCOME_BLOCKED, OUTCOME_AWAITING, OUTCOME_ERROR)


@dataclass(frozen=True)
class ForgeExecutionReceipt:
    """Governance proof for one forge plan's trip through the execution gate."""

    request: str
    selected_agent: str = ""                  # the specialist (executor) — canonical id
    selected_loadout: str = ""
    selected_skills: Tuple[str, ...] = ()
    required_weapons: Tuple[str, ...] = ()
    action_class: str = ""                    # safe / risky / destructive
    approval_level: str = ""                  # autopilot.approval L*
    authorized: bool = False
    outcome: str = OUTCOME_AWAITING
    approval_metadata: str = ""               # decision/level/signoff(+operator)
    chain_trace: Tuple[str, ...] = ()         # PM→gateway→tech-lead trace
    commit_trailers: Tuple[str, ...] = ()     # Forgekit-Agent/Approval/... (authorized only)
    verification: Tuple[str, ...] = ()        # the plan's verification commands
    risky_weapons: Tuple[str, ...] = ()
    blocking_reasons: Tuple[str, ...] = ()
    evidence_path: str = ""

    def to_dict(self) -> dict:
        return {
            "request": self.request, "selected_agent": self.selected_agent,
            "selected_loadout": self.selected_loadout, "selected_skills": list(self.selected_skills),
            "required_weapons": list(self.required_weapons), "action_class": self.action_class,
            "approval_level": self.approval_level, "authorized": self.authorized,
            "outcome": self.outcome, "approval_metadata": self.approval_metadata,
            "chain_trace": list(self.chain_trace), "commit_trailers": list(self.commit_trailers),
            "verification": list(self.verification), "risky_weapons": list(self.risky_weapons),
            "blocking_reasons": list(self.blocking_reasons), "evidence_path": self.evidence_path,
        }

    def lines(self) -> Tuple[str, ...]:
        head = "forge execution receipt"
        status = ("인가됨" if self.authorized else "차단됨") + f" / {self.outcome}"
        out = [
            f"{head} — {status}",
            f"- request : {self.request[:70]}",
            f"- equip   : agent={self.selected_agent} loadout={self.selected_loadout}",
            f"- class   : {self.action_class} ({self.approval_level})",
            f"- approval: {self.approval_metadata or '-'}",
        ]
        if self.risky_weapons:
            out.append(f"- risky weapons: {', '.join(self.risky_weapons)}")
        if not self.authorized and self.blocking_reasons:
            out.append(f"- blocked : {'; '.join(self.blocking_reasons)}")
        return tuple(out)


def validate_forge_receipt(receipt: ForgeExecutionReceipt) -> Tuple[str, ...]:
    """Reject a fake receipt. ``()`` = the receipt's claims match a real authorization."""

    v = []
    if receipt.outcome not in OUTCOMES:
        v.append(f"receipt: outcome '{receipt.outcome}' 알 수 없음")
    # a real-plan outcome must name its request; AWAITING is the honest "nothing to forge".
    if receipt.outcome != OUTCOME_AWAITING and not (receipt.request or "").strip():
        v.append("receipt: request 비어 있음")

    if receipt.authorized:
        if not (receipt.approval_metadata or "").strip():
            v.append("receipt: authorized 인데 approval_metadata 없음 — fake 승인")
        if not is_known(receipt.selected_agent):
            v.append(f"receipt: executor '{receipt.selected_agent}' 레지스트리에 없음")
        if not receipt.commit_trailers:
            v.append("receipt: authorized 인데 commit trailer 없음 — 승인 메타데이터 미바인딩")
        if receipt.action_class == "destructive":
            v.append("receipt: destructive 가 authorized 일 수 없음")
    else:
        if not receipt.blocking_reasons:
            v.append("receipt: 미인가인데 blocking_reasons 없음 — 침묵 거부")
        if receipt.commit_trailers:
            v.append("receipt: 미인가인데 commit trailer 존재 — fake approval metadata")
        if receipt.outcome == OUTCOME_EXECUTED:
            v.append("receipt: 미인가인데 outcome=executed — fake 실행")

    if receipt.outcome == OUTCOME_EXECUTED and not receipt.authorized:
        v.append("receipt: executed 는 authorized 를 요구")
    return tuple(v)


__all__ = (
    "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING", "OUTCOME_ERROR", "OUTCOMES",
    "ForgeExecutionReceipt", "validate_forge_receipt",
)
