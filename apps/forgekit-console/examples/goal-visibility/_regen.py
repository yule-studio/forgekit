"""Regenerate goal-visibility-evidence.txt — deterministic (tempdir goal store).
always-on runtime status 가 goal-continuity(active/awaiting/last-exec)를 operator-visible 하게 표면.
재현: tests/forgekit/test_goal_status_visibility.py
"""
from __future__ import annotations
import tempfile
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime import surface
from forgekit_runtime.runtime.goal_status import goal_continuity_status
def clk():
    n={"i":0}
    def now(): n["i"]+=1; return f"2026-06-22T07:30:{n['i']:02d}+00:00"
    return now
def banner(t): print("\n"+"="*78+f"\n{t}\n"+"="*78)
print("ForgeKit always-on runtime — goal-continuity 가시성 (operator-visible) — deterministic evidence")
print("재현: tests/forgekit/test_goal_status_visibility.py")
with tempfile.TemporaryDirectory() as home:
    env={"FORGEKIT_HOME":home}; now=clk(); st=GoalStore(env=env)
    banner("STEP 1 — goal 없음: 정직 표기(fake 진행 없음)")
    for ln in surface.daemon_status_lines(env=env):
        if "readiness" in ln or "always-on" in ln: print(ln)
    # active goal with real execution evidence + a risky goal awaiting operator
    g=transitions.apply(Goal.create("ship console help polish",now=now),GoalStatus.ACTIVE,now=now)
    g=g.add_evidence("execution","safe packet 실행 — 콘솔 도움말 문구 개선",ref="p1",now=now)
    g=g.add_evidence("verification","bounded write 재읽기 검증 통과",ref="p1",now=now); st.save(g)
    gw=transitions.apply(Goal.create("harden auth flow",now=now),GoalStatus.ACTIVE,now=now)
    gw=transitions.apply(gw,GoalStatus.AWAITING_APPROVAL,now=now); st.save(gw)
    banner("STEP 2 — active(진행) 1 + awaiting(승인 필요) 1: runtime status 가 둘 다 표면")
    for ln in surface.daemon_status_lines(env=env): print(ln)
    banner("STEP 3 — machine-readable status")
    print(goal_continuity_status(env=env).to_dict())
