"""Activation receipt — proof an external tool/skill/plugin was activated under approval.

The forge receipt proves "this forged work ran under a real approval"; this is the same
artifact for the **activation** path. It is what an operator (or an audit) reads months
later to answer: *which* external capability did the runtime attach/install/run, from
*what source*, classified *how*, approved by *whom*, and — crucially — *why was it used*
(``evidence``). That last field is the whole point of the lane: capabilities don't appear
in the runtime by accident, and the receipt is the paper trail.

Anti-fake (:func:`validate_activation_receipt`) — the rule the user named "fake
'installed' 금지":

* ``outcome`` ``enabled``/``executed`` REQUIRES ``authorized`` — a tool can never claim
  to be installed/run without a real verdict behind it;
* an ``authorized`` receipt MUST carry approval metadata, a registry-known executor, and
  commit trailers (a blocked verdict produces NONE);
* a non-authorized receipt MUST carry blocking reasons and MUST NOT carry trailers or an
  active outcome.

So "추천됨"(a candidate state) can never silently become "설치됨"/"실행됨"(a receipt).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from forgekit_config.identity.registry import is_known

from .states import ST_BLOCKED, ST_ENABLED, ST_EXECUTED

# outcome states (honest — mirror forge.receipt, but split enabled vs executed so
# "설치됨" ≠ "실행됨" stays visible in the receipt itself).
OUTCOME_ENABLED = "enabled"      # attached/installed + turned on (not necessarily run)
OUTCOME_EXECUTED = "executed"    # actually ran
OUTCOME_BLOCKED = "blocked"      # refused by the gate
OUTCOME_AWAITING = "awaiting"    # nothing to activate (honest empty)
OUTCOME_ERROR = "error"
OUTCOMES: Tuple[str, ...] = (
    OUTCOME_ENABLED, OUTCOME_EXECUTED, OUTCOME_BLOCKED, OUTCOME_AWAITING, OUTCOME_ERROR)

# the outcomes that assert the runtime really brought the capability live.
_ACTIVE_OUTCOMES = frozenset({OUTCOME_ENABLED, OUTCOME_EXECUTED})
# outcome → the lifecycle state the candidate lands in.
OUTCOME_TO_STATE = {OUTCOME_ENABLED: ST_ENABLED, OUTCOME_EXECUTED: ST_EXECUTED,
                    OUTCOME_BLOCKED: ST_BLOCKED}


@dataclass(frozen=True)
class ActivationReceipt:
    """Governance proof for one candidate's trip through the activation gate."""

    candidate_id: str
    kind: str = "tool"
    display_name: str = ""
    source: str = ""
    action: str = ""                          # attach / install / enable / execute
    from_state: str = ""                      # lifecycle state before
    to_state: str = ""                        # lifecycle state after (outcome state)
    disposition: str = ""                     # safe / risky / blocked
    approval_level: str = ""
    authorized: bool = False
    outcome: str = OUTCOME_AWAITING
    approval_metadata: str = ""               # decision/level/signoff(+operator)
    chain_trace: Tuple[str, ...] = ()         # PM→gateway→tech-lead trace
    commit_trailers: Tuple[str, ...] = ()     # bound to the approval (authorized only)
    executor: str = ""                        # the runtime role that activated it
    supply_chain_flags: Tuple[str, ...] = ()  # install_required / external_source / ...
    verify_command: str = ""                  # how presence is/was checked
    evidence: str = ""                        # WHY it was activated (the audit answer)
    blocking_reasons: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "kind": self.kind,
            "display_name": self.display_name, "source": self.source,
            "action": self.action, "from_state": self.from_state, "to_state": self.to_state,
            "disposition": self.disposition, "approval_level": self.approval_level,
            "authorized": self.authorized, "outcome": self.outcome,
            "approval_metadata": self.approval_metadata, "chain_trace": list(self.chain_trace),
            "commit_trailers": list(self.commit_trailers), "executor": self.executor,
            "supply_chain_flags": list(self.supply_chain_flags),
            "verify_command": self.verify_command, "evidence": self.evidence,
            "blocking_reasons": list(self.blocking_reasons),
        }

    def lines(self) -> Tuple[str, ...]:
        status = ("인가됨" if self.authorized else "차단됨") + f" / {self.outcome}"
        out = [
            f"activation receipt — {status}",
            f"- candidate: {self.kind}:{self.candidate_id} ({self.source})",
            f"- action  : {self.action}  [{self.from_state}→{self.to_state}]",
            f"- class   : {self.disposition} ({self.approval_level})",
            f"- approval: {self.approval_metadata or '-'}",
            f"- why     : {self.evidence or '-'}",
        ]
        if self.supply_chain_flags:
            out.append(f"- supply-chain: {', '.join(self.supply_chain_flags)}")
        if not self.authorized and self.blocking_reasons:
            out.append(f"- blocked : {'; '.join(self.blocking_reasons)}")
        return tuple(out)


def validate_activation_receipt(receipt: ActivationReceipt) -> Tuple[str, ...]:
    """Reject a fake receipt. ``()`` = the receipt's claims match a real authorization."""

    v = []
    if receipt.outcome not in OUTCOMES:
        v.append(f"receipt: outcome '{receipt.outcome}' 알 수 없음")
    if receipt.outcome != OUTCOME_AWAITING and not (receipt.candidate_id or "").strip():
        v.append("receipt: candidate_id 비어 있음")
    if receipt.action and receipt.outcome != OUTCOME_AWAITING:
        from .classify import ACTIONS
        if receipt.action not in ACTIONS:
            v.append(f"receipt: action '{receipt.action}' 알 수 없음")

    if receipt.authorized:
        if not (receipt.approval_metadata or "").strip():
            v.append("receipt: authorized 인데 approval_metadata 없음 — fake 승인")
        if not is_known(receipt.executor):
            v.append(f"receipt: executor '{receipt.executor}' 레지스트리에 없음")
        if not receipt.commit_trailers:
            v.append("receipt: authorized 인데 commit trailer 없음 — 승인 미바인딩")
        if receipt.disposition == "blocked":
            v.append("receipt: blocked 분류가 authorized 일 수 없음")
    else:
        if not receipt.blocking_reasons:
            v.append("receipt: 미인가인데 blocking_reasons 없음 — 침묵 거부")
        if receipt.commit_trailers:
            v.append("receipt: 미인가인데 commit trailer 존재 — fake approval metadata")
        if receipt.outcome in _ACTIVE_OUTCOMES:
            v.append(f"receipt: 미인가인데 outcome={receipt.outcome} — fake 설치/실행")

    # the headline rule: an active outcome REQUIRES a real authorization.
    if receipt.outcome in _ACTIVE_OUTCOMES and not receipt.authorized:
        v.append(f"receipt: {receipt.outcome} 는 authorized 를 요구 (fake 'installed' 금지)")
    return tuple(v)


__all__ = (
    "OUTCOME_ENABLED", "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING",
    "OUTCOME_ERROR", "OUTCOMES", "OUTCOME_TO_STATE",
    "ActivationReceipt", "validate_activation_receipt",
)
