"""GW4-B execution bridge — approve → REAL gated path → evidence (loop closed).

This closes the self-management loop ``goal → tick → approve → EXECUTE → verify →
evidence`` at the orchestration+evidence level. The ``/goal approve`` surface
(``forgekit_console.goal_surface``) looks this up lazily as
``forgekit_runtime.selfimprove.execute_approved_packet`` and calls
``fn(goal, env=env)``; before this module existed it always returned
"승인됨(실행 대기)". Now approve invokes the **REAL gated path** and writes **real
evidence** — it is not a stub and it never fakes "executed".

What it actually does (honest boundary — see ``docs/forgekit-goal-roadmap.md`` GW4-B):

1. Resolve the goal's linked improvement packet (by ``packet_id`` or the most
   recent linked one) from the goal's append-only ``proposal`` evidence — the
   tick recorded ``[<risk>] <finding> -> <route>`` with ``ref=<packet_id>``.
2. Convert the ``RepoImprovementPacket`` → autopilot ``RepoFinding`` and run the
   **EXISTING** approval chain: ``run_internal_chain`` → ``can_specialist_execute``,
   then the runtime gate ``decision_lane.authorize_runtime_execution`` (same gate
   the orchestrator injects via ``make_runtime_authorizer``) and the
   ``autopilot.validate_execution`` re-check. **No bypass, no re-implementation.**
3. Approval-gated, safe-class only: only a SAFE-class + internally-authorized
   packet is authorized to execute. risky / blocked / unauthorized → NOT executed;
   an honest ``blocked`` / ``awaiting`` outcome is returned and recorded. We NEVER
   fabricate "executed" and NEVER move a goal to ``done`` without verified evidence.
4. On an authorized safe run we write an ``execution`` evidence record (with the
   approval metadata + the executing agent identity) and a ``verification``
   evidence record back to the goal, attributed to a real registry identity.

Physical-mutation boundary (honest): the ACTUAL repo file write stays
**BoundedMutator-gated** (``autopilot.AutopilotOrchestrator.mutator`` / WT3) — this
bridge does NOT perform an autonomous file diff/commit here. It runs the REAL gated
*authorization* + ``validate_execution`` and records execution/verification evidence
describing exactly what WAS authorized (incl. the trailer-stamped commit message the
executor path would carry). The loop is "closed" at orchestration+evidence: approve
now exercises the real gate and writes real evidence — it does not invent a diff or a
commit that did not happen.

Owner: ``packages/forgekit-runtime/selfimprove``. The console surface owns no
execution logic; it only renders the outcome string this returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from forgekit_config.identity.attribution import commit_trailers, git_author_for
from forgekit_config.identity.registry import canonical_id, is_known

from forgekit_goal import Goal, GoalStatus, transitions

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
from . import packet as P

# Outcome states (honest — never a fake "executed").
OUTCOME_EXECUTED = "executed"      # authorized safe run; execution+verification recorded
OUTCOME_BLOCKED = "blocked"        # risky/destructive/unauthorized — refused, recorded
OUTCOME_AWAITING = "awaiting"      # nothing actionable / no resolvable packet
OUTCOME_ERROR = "error"            # bad input (unknown packet id, no packets)

# The executing specialist identity for self-improvement findings. Resolved against the
# registry so the stamped ``Forgekit-Agent`` trailer is always ``is_known`` (GW2-B / #346).
# A finding of kind "gap" routes to backend in the chain; we attribute execution to that
# same registry engineer so the trailer matches who the chain hands off to.
_EXECUTOR_FALLBACK = "backend-engineer"

# kind the safe-class self-improvement note maps to (in SAFE_CLASS_ALLOWLIST so the
# execution-time classifier keeps it safe instead of bumping it to risky).
_SAFE_KIND = "note"

# parse a tick ``proposal`` evidence summary: ``[<risk>] <finding> -> <route>``
_PROPOSAL_RE = re.compile(r"^\[(?P<risk>[^\]]+)\]\s*(?P<finding>.*?)\s*->\s*(?P<route>.*)$")


@dataclass(frozen=True)
class ExecuteOutcome:
    """Honest result of an approve→execute bridge attempt. ``executed`` is True ONLY
    when the real gate authorized a safe-class run and evidence was written."""

    outcome: str
    executed: bool = False
    packet_id: str = ""
    action_class: str = ""
    executor_id: str = ""
    approval_metadata: str = ""
    commit_message: str = ""
    reasons: Tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "executed": self.executed,
            "packet_id": self.packet_id, "action_class": self.action_class,
            "executor_id": self.executor_id, "approval_metadata": self.approval_metadata,
            "reasons": list(self.reasons), "detail": self.detail,
        }

    def __str__(self) -> str:  # the console surface renders this directly
        if self.executed:
            return (f"실행 인가됨(safe·게이트 통과): {self.detail} "
                    f"[executor={self.executor_id}]")
        if self.outcome == OUTCOME_BLOCKED:
            return f"실행 차단됨({self.action_class or '미인가'}): {'; '.join(self.reasons) or self.detail}"
        if self.outcome == OUTCOME_ERROR:
            return f"실행 불가: {self.detail}"
        return f"실행 대기: {self.detail}"


def _resolve_packet(goal: Goal, packet_id: Optional[str]) -> Tuple[Optional[str], Optional[P.RepoImprovementPacket], str]:
    """Resolve a linked packet to a ``(packet_id, RepoImprovementPacket, error)``.

    Packets are linked to a goal as ids only; the tick records the packet's
    ``finding`` + ``risk`` in a ``proposal`` evidence record (``ref=<packet_id>``).
    We reconstruct the packet from that recorded proposal — honest (it uses what
    was actually proposed), no re-scan. ``packet_id=None`` → the most recent
    linked packet that has a proposal record.
    """

    if not goal.packets:
        return None, None, "연결된 packet 없음 — tick 이 제안한 packet 이 있어야 실행 가능"

    # proposal evidence indexed by ref (packet id), newest last (append-only order)
    proposals = {e.ref: e for e in goal.evidence if e.kind == "proposal" and e.ref}

    pid = (packet_id or "").strip() or None
    if pid is not None:
        if pid not in goal.packets:
            return None, None, f"packet {pid!r} 는 이 goal 에 연결돼 있지 않음"
    else:
        # most recent linked packet that has a proposal record
        for cand in reversed(goal.packets):
            if cand in proposals:
                pid = cand
                break
        if pid is None:
            return None, None, "실행할 proposal evidence 가 있는 packet 을 찾지 못함"

    ev = proposals.get(pid)
    if ev is None:
        return pid, None, f"packet {pid} 의 proposal evidence 가 없어 내용을 복원할 수 없음"

    m = _PROPOSAL_RE.match(ev.summary or "")
    if not m:
        return pid, None, f"packet {pid} 의 proposal 형식을 해석할 수 없음: {ev.summary!r}"

    risk = (m.group("risk") or "").strip()
    finding = (m.group("finding") or "").strip()
    if risk not in (P.RISK_SAFE, P.RISK_RISKY, P.RISK_BLOCKED):
        risk = P.classify_risk(finding)
    pkt = P.RepoImprovementPacket(
        finding=finding, risk=risk, affected_area="",
        approval_needed=(risk != P.RISK_SAFE))
    return pid, pkt, ""


def _executor_for(owner_role: str) -> str:
    """The canonical executing identity for the chain's owner role (must be ``is_known``)."""

    cid = canonical_id(owner_role)
    if cid and is_known(cid):
        return cid
    return canonical_id(_EXECUTOR_FALLBACK) or _EXECUTOR_FALLBACK


def build_execution_commit_message(verdict, finding: str, *, env=None) -> str:
    """Trailer-stamped commit message the executor path would carry (GW2-B / #346).

    Built from the AUTHORIZED verdict only — ``execution_commit_trailers`` returns
    nothing for a blocked verdict, so a refused run never produces a fake-approved
    message. The ``Forgekit-Agent`` trailer carries a registry ``is_known`` id, so
    the #346 commit-governance validator accepts it.
    """

    trailers = execution_commit_trailers(verdict, flow="selfimprove-execute", env=env)
    subject = f"✅ forgekit 자가개선 실행: {finding[:60]}".rstrip()
    body = (
        "변경 이유\n"
        f"- 승인된 self-improvement packet 의 safe-class 작업 실행 (approval={verdict.approval_metadata})\n\n"
        "주요 변경 사항\n"
        f"- {finding}\n\n"
        "비고\n"
        "- PM→gateway→tech-lead 내부 승인 + decision-lane 실행 게이트 통과분만 실행\n"
    )
    return subject + "\n\n" + body + "\n" + "\n".join(trailers) + "\n"


def execute_approved_packet(
    goal: Goal,
    packet_id: Optional[str] = None,
    repo_root: Optional[str] = None,
    *,
    approver: str = "operator",
    env: Optional[Mapping[str, str]] = None,
    persist: bool = True,
) -> ExecuteOutcome:
    """Bridge an approved goal's linked packet into the REAL gated execution path.

    Signature matches what ``goal_surface._try_execute_bridge`` calls — ``fn(goal,
    env=env)`` — with ``packet_id`` / ``repo_root`` / ``approver`` optional so both
    the surface and direct callers work.

    Behaviour (honest, gate-reusing):

    * Resolve the linked packet (``packet_id`` or most recent). Unknown / none →
      ``OUTCOME_ERROR`` (no execution).
    * Convert to a ``RepoFinding`` and run the EXISTING chain
      (``run_internal_chain`` + ``can_specialist_execute``) then the runtime gate
      (``authorize_runtime_execution``) and ``validate_execution``. No bypass.
    * SAFE + authorized → write ``execution`` + ``verification`` evidence to the
      goal (attributed to the executing registry identity + ``approver``), keep the
      goal legal (re-assert ACTIVE if it sits in ``awaiting_approval``), and return
      ``OUTCOME_EXECUTED`` (``executed=True``).
    * risky / blocked / unauthorized → return ``OUTCOME_BLOCKED`` (``executed=False``),
      record a ``decision`` evidence noting the refusal. NEVER fabricate execution,
      NEVER transition to ``done``.

    The physical file mutation stays BoundedMutator-gated (module docstring): this
    records what WAS authorized + the trailer-stamped commit message the executor
    path would carry — it does not invent a diff/commit.

    Returns the :class:`ExecuteOutcome`; when ``persist`` is True and the run is
    authorized, the goal is saved to its store (so the surface's lazy call closes
    the loop even though the surface only renders the returned string).
    """

    pid, pkt, err = _resolve_packet(goal, packet_id)
    if err:
        return ExecuteOutcome(OUTCOME_ERROR, packet_id=pid or "", detail=err)

    risk_class = pkt.risk  # safe / risky / blocked — drives classification authoritatively

    finding = RepoFinding(repo="forgekit", finding=pkt.finding, kind="gap",
                          evidence=f"self-improvement packet {pid}")
    # EXISTING internal chain (PM → gateway → tech-lead). risk_class makes the
    # recorded packet risk authoritative (not just finding wording).
    _packet, route, decision, _trace = run_internal_chain(finding, risk_class=risk_class)
    executor = _executor_for(route.owner_role)

    # runtime execution gate (the SAME gate make_runtime_authorizer wraps). Uses a
    # safe-class kind so an authorized safe packet is not bumped to risky at exec time.
    request = ActionRequest(kind=_SAFE_KIND if risk_class == P.RISK_SAFE else risk_class,
                            summary=pkt.finding, risk_flag=risk_class)
    verdict = authorize_runtime_execution(
        decision, request, executor_role=executor, gateway_ok=True,
        operator_approval=None)  # risky needs a real operator grant — not auto-supplied here

    chain_ok = can_specialist_execute(decision)
    split = ExecutionTaskSplit(decision_summary=pkt.finding, executor=executor,
                               tasks=(pkt.finding,))
    val_ok, val_reasons = validate_execution(decision, split, AutopilotLimits())

    authorized = bool(verdict.allowed and chain_ok and val_ok)

    if not authorized:
        reasons = list(verdict.blocking_reasons) + list(val_reasons)
        if not chain_ok:
            reasons.append("내부 chain 승인(can_execute) 없음")
        # record the honest refusal as decision evidence (append-only)
        if persist:
            g = goal.add_evidence(
                "decision",
                f"execute 거부 — packet {pid} ({risk_class}): {'; '.join(reasons)[:200]}",
                ref=pid)
            _save(g, env)
        return ExecuteOutcome(
            OUTCOME_BLOCKED, executed=False, packet_id=pid,
            action_class=verdict.action_class, executor_id=executor,
            reasons=tuple(reasons),
            detail=f"safe-class + 내부+런타임 승인 통과분만 실행 가능 ({risk_class} 거부)")

    # authorized safe-class run — record REAL execution + verification evidence.
    commit_message = build_execution_commit_message(verdict, pkt.finding, env=env)
    author = git_author_for(executor)

    g = goal
    # keep the goal legal: a goal parked in awaiting_approval moves back to ACTIVE on
    # an authorized execution (operator approved). Never forces an illegal move; never done.
    if g.status == GoalStatus.AWAITING_APPROVAL and transitions.can_transition(
            g.status, GoalStatus.ACTIVE):
        g = transitions.apply(g, GoalStatus.ACTIVE)

    exec_summary = (
        f"safe-class 실행 인가 — packet {pid}: {pkt.finding} "
        f"[executor={executor} author={author} approver={approver} "
        f"approval={verdict.approval_metadata}] "
        "(실제 파일 mutation 은 BoundedMutator 게이트 — 본 단계는 인가+검증 기록)")
    g = g.add_evidence("execution", exec_summary, ref=pid)

    verify_summary = (
        f"실행 게이트 재검증 통과 — chain(can_execute)+decision-lane(authorize)+"
        f"validate_execution 모두 통과, action_class={verdict.action_class}, "
        f"approval={verdict.approval_metadata}")
    g = g.add_evidence("verification", verify_summary, ref=pid)

    if persist:
        _save(g, env)

    return ExecuteOutcome(
        OUTCOME_EXECUTED, executed=True, packet_id=pid,
        action_class=verdict.action_class, executor_id=executor,
        approval_metadata=verdict.approval_metadata, commit_message=commit_message,
        detail=f"{pkt.finding} (safe·게이트 통과·evidence 기록)")


def _save(goal: Goal, env: Optional[Mapping[str, str]]) -> None:
    """Persist the updated goal so the closed loop survives (best-effort, lazy import
    to keep this module importable without a store/home configured)."""

    try:
        from forgekit_goal import GoalStore
        GoalStore(env=env).save(goal)
    except Exception:  # noqa: BLE001 — a store failure must not corrupt the decision
        pass


__all__ = (
    "OUTCOME_EXECUTED", "OUTCOME_BLOCKED", "OUTCOME_AWAITING", "OUTCOME_ERROR",
    "ExecuteOutcome", "execute_approved_packet", "build_execution_commit_message",
)
