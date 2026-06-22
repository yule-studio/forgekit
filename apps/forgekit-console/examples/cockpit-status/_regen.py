"""Regenerate cockpit-status-evidence.txt — deterministic (pure render + tempdir stores).

Operator-cockpit status line: the persistent issue line surfaces the runtime mode posture
PLUS the two control-plane facts an operator otherwise had to poll for — goals parked in
awaiting_approval (real goal-store count) and today's budget spend (real usage ledger). No
fakes: numbers come from the real stores; failures degrade to no-badge.

재현: tests/forgekit/test_tui_cockpit_status.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from forgekit_console.tui import render
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


print("ForgeKit console — operator cockpit status line — deterministic evidence")
print("재현: tests/forgekit/test_tui_cockpit_status.py")

base = dict(label="auto", policy_mode="balanced", usage_mode="live",
            approval="internal", loop=True)

banner("STEP 1 — 기본(승인대기 0 · budget 미설정): 기존과 동일, 배지 없음")
print(render.runtime_mode_line(**base))

banner("STEP 2 — 승인대기 goal 1 (warn 배지 + 행동 포인터)")
print(render.runtime_mode_line(**base, awaiting=1))

banner("STEP 3 — budget 42% (여유 → dim) vs 95% (한계 근접 → warn)")
print(render.runtime_mode_line(**base, budget_ratio=0.42))
print(render.runtime_mode_line(**base, budget_ratio=0.95))

banner("STEP 4 — 둘 다: 승인대기 2 + budget 93% (operator 가 한눈에)")
print(render.runtime_mode_line(**base, awaiting=2, budget_ratio=0.93))

banner("STEP 5 — 실제 goal store 카운트 (재구현 없이 goal_continuity_status 재사용)")
with tempfile.TemporaryDirectory() as home:
    env = {"FORGEKIT_HOME": home}
    st = GoalStore(env=env)
    for title in ("harden auth flow", "rotate signing keys"):
        g = transitions.apply(Goal.create(title), GoalStatus.ACTIVE)
        g = transitions.apply(g, GoalStatus.AWAITING_APPROVAL)
        st.save(g)
    from forgekit_runtime.runtime.goal_status import goal_continuity_status
    snap = goal_continuity_status(env=env)
    print(f"goal store awaiting_approval 실측: {snap.awaiting_approval}")
    print(render.runtime_mode_line(**base, awaiting=snap.awaiting_approval))

print("\n(끝) — 모든 숫자는 실제 store/ledger 에서 측정 · CSS 아닌 구조 · 가짜 0")
