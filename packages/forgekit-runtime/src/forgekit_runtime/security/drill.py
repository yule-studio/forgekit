"""Red/blue drill builder + gate (WT5) — own-assets only, plan-first, approval-gated.

The gate is the safety boundary: a drill is built ONLY for an eligible target
(allowlisted + own + isolated); ineligible → ``blocked`` with a reason (no plan that
could be aimed elsewhere). Even for an eligible target the result is ``plan-only``
(dry-run) unless the operator explicitly approves an active drill. Purple synthesis
turns red hypotheses into a blue DefenseRunbook. No offensive tooling is emitted.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from . import contract as C

# A default own-asset allowlist (operator can extend via config). Public internet /
# third-party hosts are NEVER here — and the eligibility check rejects them anyway.
_DEFAULT_ALLOWLIST = {
    "k3s-isolated": C.TargetSpec("k3s-isolated", C.TARGET_K3S_NAMESPACE, allowlisted=True,
                                 isolated=True, note="전용 격리 k3s namespace (내 인프라)"),
    "localhost": C.TargetSpec("localhost", C.TARGET_LOCALHOST, allowlisted=True,
                              isolated=True, note="로컬 개발 환경"),
}


def resolve_target(target_id: str, allowlist: Optional[Mapping[str, C.TargetSpec]] = None) -> C.TargetSpec:
    """Resolve a target id against the allowlist. Unknown → a NON-eligible spec."""

    al = allowlist or _DEFAULT_ALLOWLIST
    spec = al.get((target_id or "").strip())
    if spec is not None:
        return spec
    # unknown / not on the allowlist → explicitly ineligible (kind 'unknown')
    return C.TargetSpec(target_id or "(none)", "unknown", allowlisted=False, isolated=False,
                        note="allowlist 에 없음 — 대상 불가")


def _attack_plan(target: C.TargetSpec) -> C.AttackPlan:
    return C.AttackPlan(
        target_id=target.id,
        hypotheses=("노출된 포트/엔드포인트 점검", "인증/권한 우회 경로 가설",
                    "민감 설정/secret 노출 가설"),
        checks=("열린 포트/서비스 인벤토리(읽기)", "authz 경계 점검(읽기)",
                "로그/감사 커버리지 점검"),
        dry_run=True,  # always starts dry-run
    )


def _defense_runbook(target: C.TargetSpec) -> C.DefenseRunbook:
    return C.DefenseRunbook(
        target_id=target.id,
        hardening=("최소권한 재검토", "불필요 포트/서비스 차단", "secret 매니저로 이전"),
        detection=("이상 접근 로깅/알림", "authz 실패 모니터"),
        mitigation=("rate limit / WAF 규칙", "격리 namespace 정책 강화"),
    )


def build_drill(target_id: str, *, approved: bool = False,
                allowlist: Optional[Mapping[str, C.TargetSpec]] = None) -> C.SecurityDrillPacket:
    """Build a drill packet. Ineligible target → blocked; eligible → plan-only unless
    *approved* (then approved-active, but still operator-gated upstream)."""

    target = resolve_target(target_id, allowlist)
    runbook = _defense_runbook(target)
    if not target.eligible:
        return C.SecurityDrillPacket(
            target=target,
            attack_plan=C.AttackPlan(target_id=target.id, dry_run=True),
            defense_runbook=runbook, status=C.DRILL_BLOCKED, requires_approval=True,
            refusal_reason="대상이 allowlist 의 격리된 내 자산이 아님 — 드릴 거부 (공용/3rd-party 금지)",
        )
    plan = _attack_plan(target)
    if approved:
        # operator explicitly approved an ACTIVE drill on an eligible own asset.
        plan = C.AttackPlan(plan.target_id, plan.hypotheses, plan.checks, dry_run=False)
        return C.SecurityDrillPacket(target, plan, runbook,
                                     status=C.DRILL_APPROVED_ACTIVE, requires_approval=False)
    return C.SecurityDrillPacket(target, plan, runbook,
                                 status=C.DRILL_PLAN_ONLY, requires_approval=True)


def synthesize_purple(red: C.AttackPlan, findings: Tuple[str, ...] = ()) -> C.DefenseRunbook:
    """Purple: turn red hypotheses/findings into a blue DefenseRunbook."""

    hardening = tuple(f"{h} → 하드닝 항목화" for h in red.hypotheses)
    return C.DefenseRunbook(
        target_id=red.target_id, hardening=hardening,
        detection=tuple(f"{c} 모니터링" for c in red.checks),
        mitigation=tuple(f"발견: {f} → 완화" for f in findings) or ("발견 없음 — 예방 하드닝 유지",),
    )


def k3s_isolation_runbook() -> str:
    """The operator runbook for the isolated k3s drill environment (markdown)."""

    return (
        "# Runbook — red/blue 격리 k3s 환경\n\n"
        "- 전용 namespace(예: `forgekit-drill`) + NetworkPolicy 로 외부/타 namespace 격리.\n"
        "- 드릴 대상은 이 namespace 안 내 자산만. 공용 인터넷/3rd-party 금지.\n"
        "- 기본 dry-run/plan-only. active 드릴은 operator 승인 후에만.\n"
        "- offensive tooling 은 일반 모드에 노출하지 않음 — 계획/방어 runbook 만.\n"
    )


__all__ = ("resolve_target", "build_drill", "synthesize_purple", "k3s_isolation_runbook",
           "_DEFAULT_ALLOWLIST")
