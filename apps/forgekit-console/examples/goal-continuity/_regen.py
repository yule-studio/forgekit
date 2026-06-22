"""Regenerate goal-continuity-evidence.txt — deterministic (temp git repo + temp goal store).

G1: the always-on serve loop physically ADVANCES active goals (goal-driven execution continuity).
Run from repo root with packages on PYTHONPATH; redirect stdout into the .txt.
Regression: tests/forgekit/test_goal_exec_tick.py.
"""
from __future__ import annotations
import subprocess, tempfile
from pathlib import Path
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.autopilot.runner import BoundedMutator
from forgekit_runtime.runtime.goal_exec_tick import execute_active_goals
from forgekit_runtime.selfimprove import goal_tick

class _Signal:
    def __init__(self, t): self.text = t
def _clock():
    n={"i":0}
    def now():
        n["i"]+=1; return f"2026-06-22T06:00:{n['i']:02d}+00:00"
    return now
def _git(repo,*a): return subprocess.run(["git","-C",repo,*a],capture_output=True,text=True,check=True)
def _init(p):
    _git(p,"init","-q"); _git(p,"config","user.email","seed@forgekit.local"); _git(p,"config","user.name","seed")
    (Path(p)/"README.md").write_text("# seed\n",encoding="utf-8"); _git(p,"add","README.md"); _git(p,"commit","-q","-m","seed")
def banner(t): print("\n"+"="*78+f"\n{t}\n"+"="*78)

print("ForgeKit goal-driven execution continuity (G1) — deterministic evidence (no fake)")
print("always-on serve tick → ACTIVE goal 의 다음 safe packet 물리 실행. 재현: tests/forgekit/test_goal_exec_tick.py")
with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
    _init(repo); env={"FORGEKIT_HOME":home}; now=_clock()
    def mut(): return BoundedMutator(repo_root=Path(repo))
    # one ACTIVE goal with a safe proposal + one with a risky proposal
    gs=Goal.create("self-manage ForgeKit",mode="auto",now=now); gs=transitions.apply(gs,GoalStatus.ACTIVE,now=now)
    gs=goal_tick.tick_goal(gs,repo,signals=[_Signal("콘솔 도움말 문구 개선")],now=now).goal; GoalStore(env=env).save(gs)
    gr=Goal.create("harden auth",mode="auto",now=now); gr=transitions.apply(gr,GoalStatus.ACTIVE,now=now)
    gr=goal_tick.tick_goal(gr,repo,signals=[_Signal("auth 권한 흐름 대규모 변경")],now=now).goal; GoalStore(env=env).save(gr)

    banner("STEP 1 — goal 상태: safe goal=ACTIVE(packet 제안됨), risky goal=awaiting_approval")
    for g in GoalStore(env=env).load_all():
        print(f"  {g.id} status={g.status.value} packets={len(g.packets)} title={g.title!r}")

    banner("STEP 2 — serve tick (execute_active_goals): safe 물리 실행, risky 는 operator 대기")
    rep=execute_active_goals(repo,mut(),env=env)
    print(f"  executed={rep.executed} blocked={rep.blocked} awaiting={rep.awaiting} touched={list(rep.goals_touched)}")
    head=_git(repo,"rev-parse","HEAD").stdout.strip(); msg=_git(repo,"show","--no-patch","--format=%s",head).stdout.strip()
    print(f"  REAL commit: {head[:12]} · {msg}")
    kinds=[e.kind for e in GoalStore(env=env).get(gs.id).evidence]
    print(f"  safe goal evidence kinds: {kinds}  (execution+verification 기록 → 루프 닫힘)")

    banner("STEP 3 — 다음 tick (continuity): 이미 실행된 packet 은 dedupe, 더 진행할 safe step 없음")
    rep2=execute_active_goals(repo,mut(),env=env)
    print(f"  executed={rep2.executed} (재실행 안 함) awaiting={rep2.awaiting} (risky goal 은 여전히 operator 대기)")

    banner("STEP 4 — 정직 경계: risky goal 은 /goal approve(operator) 전엔 절대 자동 실행 안 됨")
    print(f"  risky goal {gr.id} status={GoalStore(env=env).get(gr.id).status.value} (awaiting_approval, executed 안 됨)")
