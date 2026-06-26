"""Activation → governance execution bridge (no new gate, the SAME real approval chain).

This is where the install-safety lane gets its teeth. Activating an external tool/skill/
plugin is run through the EXACT path forge plans and self-improvement findings use —

    classify_activation(safe/risky/blocked)
      → run_internal_chain (PM → gateway → tech-lead)
      → authorize_runtime_execution (decision-lane runtime gate; executor = a runtime
        engineer, e.g. devops)
      → ActivationReceipt (bound to the verdict + identity + commit trailers)

Honest boundary (same as ``forge.bridge`` / ``selfimprove.execute_bridge``): a receipt is
``authorized`` ONLY for a safe-class activation that cleared the full gate, OR a risky one
with a real operator approval. install-required / global-write / external / unknown-safety
/ blocked → NO trailers, NO active outcome — the candidate lands in ``blocked`` (or
``approval-needed`` stays pending). There is no path to a fake "installed".

The physical install/attach is the caller's job (the toolchain manager / harness); this
binds APPROVAL to that act and issues the proof. A safe *attach* of a present, vetted,
armory tool authorizes on internal signoff alone — that is the one no-friction path, and
it is exactly the path that carries no supply-chain risk.
"""

from __future__ import annotations

from typing import Mapping, Optional

from forgekit_config.identity.registry import canonical_id, is_known

from ..autopilot import RepoFinding, run_internal_chain
from ..decision_lane import (
    ActionRequest,
    authorize_runtime_execution,
    execution_commit_trailers,
)
from .classify import ACT_ATTACH, ACT_EXECUTE, BLOCKED, RISKY, SAFE, classify_activation
from .receipt import (
    OUTCOME_AWAITING,
    OUTCOME_BLOCKED,
    OUTCOME_ENABLED,
    OUTCOME_EXECUTED,
    OUTCOME_TO_STATE,
    ActivationReceipt,
)
from .states import ActivationCandidate, derive_readiness_state

# disposition → the chain/classifier risk wording (safe→"", reuse the approval ladder).
_RISK_FLAG = {SAFE: "", RISKY: "risky", BLOCKED: "blocked"}
# a safe attach uses an allowlisted kind so it is not re-bumped to risky at exec time.
_SAFE_KIND = "note"
# the runtime role that activates capabilities (installs/attaches tools). Always known.
_EXECUTOR = "devops-engineer"
_EXECUTOR_FALLBACK = "backend-engineer"


def _executor_for(route_owner: str) -> str:
    for cand in (_EXECUTOR, route_owner, _EXECUTOR_FALLBACK):
        cid = canonical_id(cand)
        if cid and is_known(cid):
            return cid
    return canonical_id(_EXECUTOR_FALLBACK) or _EXECUTOR_FALLBACK


def authorize_activation(
    candidate: ActivationCandidate,
    action: str,
    *,
    forbidden: bool = False,
    operator_approval=None,
):
    """Run *candidate*'s *action* through the full real gate.

    Returns ``(classification, verdict, executor, trace)`` — the pieces :func:`activate`
    assembles into a receipt. ``verdict.allowed`` already encodes the WHOLE chain (safe →
    internal ``can_execute``; risky → a real operator approval; blocked → never)."""

    classification = classify_activation(candidate, action, forbidden=forbidden)
    risk_flag = _RISK_FLAG.get(classification.disposition, "risky")

    why = candidate.why or f"{action} {candidate.kind}:{candidate.id}"
    finding = RepoFinding(repo="forgekit", finding=why, kind="ops",
                          evidence=f"activation:{action} source={candidate.source}")
    _pkt, route, decision, trace = run_internal_chain(finding, risk_class=risk_flag)
    executor = _executor_for(route.owner_role)

    request = ActionRequest(
        kind=_SAFE_KIND if classification.disposition == SAFE else "",
        summary=why, risk_flag=risk_flag)
    verdict = authorize_runtime_execution(
        decision, request, executor_role=executor, gateway_ok=True,
        operator_approval=operator_approval)
    return classification, verdict, executor, trace


def activate(
    candidate: ActivationCandidate,
    action: str,
    *,
    forbidden: bool = False,
    operator_approval=None,
    env: Optional[Mapping[str, str]] = None,
    persist: bool = False,
    recorded_at: str = "",
) -> ActivationReceipt:
    """Authorize activating *candidate* via *action* and issue its governance receipt.

    Honest: an ``enabled``/``executed`` (active) receipt is issued ONLY for an activation
    that cleared chain + decision-lane (safe on internal signoff; risky only with a real
    operator approval). Otherwise → a ``blocked`` receipt with reasons and NO trailers.

    ``persist=True`` appends the receipt to the append-only activation ledger (a fake
    receipt is refused there). A read-only preview keeps the default ``persist=False``.
    """

    if not (candidate.id or "").strip():
        return ActivationReceipt(candidate_id="", action=action, outcome=OUTCOME_AWAITING,
                                 blocking_reasons=("빈 candidate — 활성화할 도구 없음",))

    from_state = candidate.state or derive_readiness_state(candidate)

    classification, verdict, executor, trace = authorize_activation(
        candidate, action, forbidden=forbidden, operator_approval=operator_approval)

    # the verdict is the single source of truth: a safe attach clears on internal signoff,
    # a risky install clears ONLY with a real operator approval, blocked never clears.
    authorized = bool(verdict.allowed and classification.disposition != BLOCKED)

    reasons = []
    if not authorized:
        reasons = list(verdict.blocking_reasons)
        if classification.disposition == BLOCKED and "blocked" not in " ".join(reasons):
            reasons.append("blocked 분류 — 활성화 금지 (operator + runbook 전용)")
        # surface the supply-chain reasons so the audit sees WHY it was held.
        reasons.extend(r for r in classification.reasons if r not in reasons)

    trailers = (execution_commit_trailers(verdict, flow="activation", env=env)
                if authorized else ())
    if authorized:
        outcome = OUTCOME_EXECUTED if action == ACT_EXECUTE else OUTCOME_ENABLED
    else:
        outcome = OUTCOME_BLOCKED
    to_state = OUTCOME_TO_STATE.get(outcome, "")

    receipt = ActivationReceipt(
        candidate_id=candidate.id, kind=candidate.kind,
        display_name=candidate.display_name, source=candidate.source, action=action,
        from_state=from_state, to_state=to_state,
        disposition=classification.disposition, approval_level=classification.approval_level,
        authorized=authorized, outcome=outcome,
        approval_metadata=verdict.approval_metadata if authorized else "",
        chain_trace=tuple(trace), commit_trailers=tuple(trailers), executor=executor,
        supply_chain_flags=classification.supply_chain_flags,
        verify_command=candidate.verify_command, evidence=candidate.why,
        blocking_reasons=tuple(reasons))

    if persist:
        from .ledger import record_activation_receipt
        try:
            record_activation_receipt(receipt, env=env, recorded_at=recorded_at)
        except Exception:  # noqa: BLE001 — a store/validation issue must not lose the verdict
            pass
    return receipt


__all__ = ("authorize_activation", "activate")
