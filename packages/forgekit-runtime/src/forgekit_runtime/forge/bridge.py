"""Hephaistos forge → governance execution bridge (no re-implementation, real gates).

Connects the forging core to the governance backbone: a Hephaistos
:class:`ResolvedForgePlan` (request → specialist + skills + loadout + weapons + work
packet) is run through the EXACT SAME approval path the self-improvement bridge uses —

    forge(resolve) → classify(safe/risky/destructive)
      → run_internal_chain (PM → gateway → tech-lead)
      → authorize_runtime_execution (decision-lane runtime gate, the executor is the
        forge's selected specialist)
      → validate_execution (autopilot re-check)
      → ForgeExecutionReceipt (bound to the verdict + identity + commit trailers)

Honest boundary (same as ``selfimprove.execute_bridge``): ``authorized`` is True ONLY for
a safe-class plan that cleared the full gate; risky/destructive (incl. a risky/unknown
weapon) → ``blocked``, never executed, never trailer-stamped. The physical file mutation
stays BoundedMutator-gated — this binds approval to the forged work and issues the proof.
"""

from __future__ import annotations

from typing import Mapping, Optional

from forgekit_config.identity.registry import canonical_id, is_known

from ..autopilot import (
    AutopilotLimits,
    ExecutionTaskSplit,
    RepoFinding,
    can_specialist_execute,
    run_internal_chain,
    validate_execution,
)
from ..decision_lane import (
    ActionRequest,
    authorize_runtime_execution,
    execution_commit_trailers,
)
from .classify import DESTRUCTIVE, RISKY, SAFE, classify_forge_plan
from .receipt import (
    OUTCOME_AWAITING,
    OUTCOME_BLOCKED,
    OUTCOME_ERROR,
    OUTCOME_EXECUTED,
    ForgeExecutionReceipt,
)

# action class → the chain/classifier risk_class wording (safe→"", reuse approval ladder)
_RISK_FLAG = {SAFE: "", RISKY: "risky", DESTRUCTIVE: "blocked"}
# safe-class kind so an authorized safe plan is not re-bumped to risky at exec time
_SAFE_KIND = "note"
_EXECUTOR_FALLBACK = "backend-engineer"


def _executor_for(plan, route_owner: str) -> str:
    """The forge already selected a specialist — use it if registry-known, else the
    chain's routed owner, else the fallback engineer (always ``is_known``)."""

    for cand in (getattr(plan, "selected_agent", ""), route_owner, _EXECUTOR_FALLBACK):
        cid = canonical_id(cand)
        if cid and is_known(cid):
            return cid
    return canonical_id(_EXECUTOR_FALLBACK) or _EXECUTOR_FALLBACK


def authorize_forge_plan(plan, *, weapon_safety=None, operator_approval=None):
    """Run *plan* through the full real gate. Returns ``(receipt-fields dict)`` pieces via
    a tuple ``(classification, verdict, chain_ok, val_ok, val_reasons, executor, trace)``."""

    classification = classify_forge_plan(plan, weapon_safety=weapon_safety)
    risk_flag = _RISK_FLAG.get(classification.action_class, "risky")

    goal = getattr(plan, "request", "") or getattr(getattr(plan, "packet_draft", None), "goal", "")
    finding = RepoFinding(repo="forgekit", finding=goal, kind="gap",
                          evidence="hephaistos forge plan")
    _pkt, route, decision, trace = run_internal_chain(finding, risk_class=risk_flag)
    executor = _executor_for(plan, route.owner_role)

    request = ActionRequest(kind=_SAFE_KIND if classification.action_class == SAFE else "",
                            summary=goal, risk_flag=risk_flag)
    verdict = authorize_runtime_execution(
        decision, request, executor_role=executor, gateway_ok=True,
        operator_approval=operator_approval)

    chain_ok = can_specialist_execute(decision)
    split = ExecutionTaskSplit(decision_summary=goal, executor=executor, tasks=(goal,))
    val_ok, val_reasons = validate_execution(decision, split, AutopilotLimits())
    return classification, verdict, chain_ok, val_ok, val_reasons, executor, trace


def forge_execute(
    request: str,
    *,
    preferred_role: str = "",
    operator_approval=None,
    weapon_safety=None,
    env: Optional[Mapping[str, str]] = None,
    persist: bool = False,
    recorded_at: str = "",
) -> ForgeExecutionReceipt:
    """Forge a plan for *request* and issue its governance execution receipt.

    Honest: ``authorized`` (and thus a trailer-stamped, executed receipt) only for a
    safe-class plan that cleared chain + decision-lane + validate_execution. risky /
    destructive / risky-weapon → blocked receipt with reasons and NO trailers.

    ``persist=True`` appends the receipt to the append-only decision log (only a
    validation-passing receipt is recorded — the ledger refuses fakes). A read-only
    preview (e.g. the /resolve surface) keeps the default ``persist=False``."""

    if not (request or "").strip():
        return ForgeExecutionReceipt(request="", outcome=OUTCOME_AWAITING,
                                     blocking_reasons=("빈 요청 — forge 할 작업 없음",))

    # forge the plan (lazy import keeps the module importable without hephaistos installed)
    try:
        from hephaistos import resolve
    except Exception as e:  # noqa: BLE001
        return ForgeExecutionReceipt(request=request, outcome=OUTCOME_ERROR,
                                     blocking_reasons=(f"hephaistos 미가용: {e}",))
    plan = resolve(request, preferred_role=preferred_role)

    classification, verdict, chain_ok, val_ok, val_reasons, executor, trace = \
        authorize_forge_plan(plan, weapon_safety=weapon_safety, operator_approval=operator_approval)

    authorized = bool(verdict.allowed and chain_ok and val_ok)

    reasons = []
    if not authorized:
        reasons = list(verdict.blocking_reasons) + list(val_reasons)
        if not chain_ok:
            reasons.append("내부 chain 승인(can_execute) 없음 — safe-class 자동 실행만")

    # trailers bind to the AUTHORIZED verdict only (no fake approval on a blocked path)
    trailers = execution_commit_trailers(verdict, flow="hephaistos-forge", env=env) if authorized else ()
    outcome = OUTCOME_EXECUTED if authorized else OUTCOME_BLOCKED
    packet = getattr(plan, "packet_draft", None)

    receipt = ForgeExecutionReceipt(
        request=request,
        selected_agent=executor,
        selected_loadout=getattr(plan, "selected_loadout", ""),
        selected_skills=tuple(getattr(plan, "selected_skills", ()) or ()),
        required_weapons=tuple(getattr(plan, "required_weapons", ()) or ()),
        action_class=classification.action_class,
        approval_level=classification.approval_level,
        authorized=authorized,
        outcome=outcome,
        approval_metadata=verdict.approval_metadata if authorized else "",
        chain_trace=tuple(trace),
        commit_trailers=tuple(trailers),
        verification=tuple(getattr(plan, "verification_commands", ()) or ()),
        risky_weapons=classification.risky_weapons,
        blocking_reasons=tuple(reasons),
        evidence_path=getattr(packet, "evidence_path", "") if packet else "",
    )

    if persist:
        # append-only decision log; refuses to persist a fake receipt (best-effort I/O).
        from .ledger import record_forge_receipt
        try:
            record_forge_receipt(receipt, env=env, recorded_at=recorded_at)
        except Exception:  # noqa: BLE001 — a store/validation issue must not lose the verdict
            pass
    return receipt


__all__ = ("authorize_forge_plan", "forge_execute")
