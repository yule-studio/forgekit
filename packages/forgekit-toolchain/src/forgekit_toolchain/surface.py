"""Console-facing toolchain surface — pure line builders + the approval-gated switch.

Mirrors ``forgekit_provider_connect.surface``: the console calls these, the logic lives
here. ``apply_switch`` is the only mutating path and it ENFORCES the gate — global /
install / destructive actions need an explicit ``approve=True`` or it returns the plan
only. With no manager it refuses honestly (no fake switch).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .detect import detect_requirements
from .manager import ToolchainManager, default_manager
from .models import (
    ToolchainProfile, SwitchPlan,
    STATE_MATCH, STATE_MISMATCH, STATE_MISSING, STATE_UNPINNED, STATE_MANAGER_MISSING,
)
from .plan import verify as _verify, plan_switch as _plan, drift as _drift
from .profile import profile_for_loadout, profile_from_requirements, merge_profiles

# status mark: ● satisfied · ◐ mismatch · ○ missing · ◌ unpinned · · manager-missing
_MARK = {STATE_MATCH: "●", STATE_UNPINNED: "◌", STATE_MISMATCH: "◐",
         STATE_MISSING: "○", STATE_MANAGER_MISSING: "·"}


def resolve_profile(root, loadout_id: str = "", *, reader=None) -> ToolchainProfile:
    """The effective profile: repo-local detection, optionally enriched by a loadout."""

    reqs = detect_requirements(root, reader=reader)
    detected = profile_from_requirements(str(root), reqs, origin=f"detected:{root}")
    lo = profile_for_loadout(loadout_id) if loadout_id else None
    return merge_profiles(detected, lo)


# ── /toolchain detect ────────────────────────────────────────────────────────
def detect_lines(root, *, reader=None) -> List[str]:
    reqs = detect_requirements(root, reader=reader)
    if not reqs:
        return ["repo-local 버전 manifest 없음 (.tool-versions/.mise.toml/.nvmrc/...).",
                "다음: `/toolchain recommend <loadout>` 로 loadout 기반 프로파일 제안."]
    out = [f"repo-local toolchain 감지 — {len(reqs)} tool (실 manifest 파싱, 추측 없음):"]
    for r in reqs:
        ver = r.version or "(버전 미지정)"
        out.append(f"  {r.tool:<10} {ver:<14} ← {r.source_file}")
    return out


# ── /toolchain recommend <loadout> ───────────────────────────────────────────
def recommend_lines(root, loadout_id: str = "", *, reader=None) -> List[str]:
    if not loadout_id:
        return ["사용법: `/toolchain recommend <loadout>` (예: backend-java-local).",
                "loadout 의 환경 가정 + weapon 을 toolchain 프로파일로 변환합니다."]
    lo = profile_for_loadout(loadout_id)
    if lo is None:
        return [f"알 수 없는 loadout: {loadout_id} — `/loadout` 로 확인."]
    from .models import SRC_LOADOUT
    prof = resolve_profile(root, loadout_id, reader=reader)
    out = [f"추천 toolchain 프로파일 — loadout `{loadout_id}` (+ repo-local 감지 병합):"]
    for t in prof.tools:
        src = "loadout" if t.source == SRC_LOADOUT else "repo"
        ver = t.version or "(미지정/present-only)"
        out.append(f"  {t.tool:<10} {ver:<18} · {src}")
    out.append("다음: `/toolchain switch` (repo-local 적용) · `/toolchain verify` (활성과 대조).")
    return out


# ── /toolchain verify  &  /toolchain drift ───────────────────────────────────
def verify_lines(root, loadout_id: str = "", *, manager: Optional[ToolchainManager] = None,
                 reader=None, drift_only: bool = False) -> List[str]:
    prof = resolve_profile(root, loadout_id, reader=reader)
    rep = _verify(prof, manager=manager)
    head = "drift" if drift_only else "verify"
    if not prof.tools:
        return [f"toolchain {head}: 프로파일 비어 있음 — 먼저 `/toolchain detect` 또는 `recommend <loadout>`."]
    if not rep.manager_available:
        return [f"toolchain {head}: mise(toolchain manager) 미설치 — 활성 버전 확인 불가.",
                "  설치: https://mise.jdx.dev  (`curl https://mise.run | sh`)",
                "  ※ 미설치 상태를 'in-sync' 로 위장하지 않음 — 검증은 manager 가 있어야 가능."]
    rows = rep.drifted if drift_only else rep.statuses
    if drift_only and not rows:
        return [f"toolchain drift: 없음 — 활성 버전이 프로파일과 일치 (verdict={rep.verdict})."]
    out = [f"toolchain {head}: verdict={rep.verdict}  (manager={rep.profile} 기준, mise current)"]
    for s in rows:
        mark = _MARK.get(s.state, "·")
        req = s.required or "(미지정)"
        act = s.active or "(없음)"
        detail = f"  — {s.detail}" if s.detail else ""
        out.append(f"  {mark} {s.tool:<10} 요구 {req:<12} · 활성 {act}{detail}")
    return out


def drift_lines(root, loadout_id: str = "", *, manager=None, reader=None) -> List[str]:
    return verify_lines(root, loadout_id, manager=manager, reader=reader, drift_only=True)


# ── /toolchain switch [global] [--approve] ───────────────────────────────────
def plan_lines(plan: SwitchPlan) -> List[str]:
    if not plan.manager_available:
        return ["  (manager 미설치 — 계획만 표시, 실행 불가)"] + [
            f"    {a.scope:<11} {' '.join(a.command)}" for a in plan.actions]
    if not plan.actions:
        return ["  변경 없음 — 활성 버전이 이미 프로파일을 만족."]
    out = []
    for a in plan.actions:
        gate = "  ⚠ 승인필요" if a.requires_approval else ""
        out.append(f"  {a.scope:<11} {' '.join(a.command)}{gate}")
        if a.reason:
            out.append(f"               └ {a.reason}")
    return out


def apply_switch(root, loadout_id: str = "", *, approve: bool = False, scope: str = "local",
                 manager: Optional[ToolchainManager] = None, reader=None) -> Tuple[bool, List[str]]:
    """Execute the switch — but only the actions the gate permits.

    - no manager → refuse honestly (no fake switch), return the would-be plan.
    - gated actions (install/global/destructive) without ``approve`` → return the plan,
      execute nothing.
    - otherwise run the permitted actions via the manager and report real exit codes.
    """

    mgr = manager or default_manager()
    prof = resolve_profile(root, loadout_id, reader=reader)
    plan = _plan(prof, manager=mgr, scope=scope)
    if not prof.tools:
        return (False, ["switch 대상 프로파일이 비어 있음 — `detect`/`recommend` 먼저."])
    if not plan.manager_available:
        return (False, ["toolchain switch 불가: mise 미설치 — fake switch 하지 않음.",
                        "  설치 후 재시도: https://mise.jdx.dev"] + plan_lines(plan))
    if not plan.actions:
        return (True, ["switch 불필요 — 활성 버전이 이미 프로파일을 만족."])
    if plan.needs_approval and not approve:
        return (False, [f"toolchain switch: 승인 필요 — {len(plan.gated)} 개 global/install/destructive 액션.",
                        *plan_lines(plan),
                        "  실행하려면: `/toolchain switch --approve` (또는 local 만이면 자동)."])
    # execute permitted actions (local always; gated only when approved).
    ran, results = [], []
    for a in plan.actions:
        if a.requires_approval and not approve:
            results.append(f"  skip(승인필요) {' '.join(a.command)}")
            continue
        code, _out = mgr.run(a.command)
        ran.append(a)
        results.append(f"  {'ok' if code == 0 else f'fail({code})'} {' '.join(a.command)}")
    ok = bool(ran) and all(not r.startswith("  fail") for r in results)
    head = [f"toolchain switch: {len(ran)} 액션 실행 (manager={mgr.name})"]
    # honest post-state via a re-verify
    post = _verify(prof, manager=mgr)
    head.append(f"  사후 검증 verdict={post.verdict}")
    return (ok, head + results)


__all__ = ("resolve_profile", "detect_lines", "recommend_lines", "verify_lines",
           "drift_lines", "plan_lines", "apply_switch")
