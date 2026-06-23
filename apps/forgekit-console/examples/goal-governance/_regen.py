"""Regenerate goal-governance-evidence.txt — deterministic (tempdir + temp git repo).
goal 루프가 PM→gateway→tech-lead→specialist artifact 흐름을 강제: big goal 분해 시 PM brief 가
첫 artifact, specialist 는 design chain executable 전엔 차단. 재현: tests/forgekit/test_goal_governance_enforcement.py
"""
from __future__ import annotations
import subprocess, tempfile
from pathlib import Path
from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.runtime.goal_scheduler_tick import GoalSchedulerTicker
from forgekit_runtime.runtime.goal_exec_tick import GoalExecTicker
from forgekit_runtime.runtime import goal_governance as gov
from forgekit_console import goal_surface as gs
from forgekit_runtime.selfimprove import packet as P
from forgekit_runtime.selfimprove.loop import SelfImprovementResult
import forgekit_runtime.decision_lane as L

def banner(t): print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

print("ForgeKit goal governance enforcement — 설계 없는 구현 금지 — deterministic evidence")
print("재현: tests/forgekit/test_goal_governance_enforcement.py · 하드레일: anti-fake(자동 decision 날조 금지)")
with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repod:
    env = {"FORGEKIT_HOME": home}; repo = Path(repod)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    store = GoalStore(env=env)
    g = transitions.apply(Goal.create("self-manage ForgeKit", mode="auto"), GoalStatus.ACTIVE)
    store.save(g)
    pkts = [P.make_packet("clarify console help", area="docs"),
            P.make_packet("add regression test", area="tests")]
    sched = GoalSchedulerTicker(repo_root=repo, env=env,
                                discover=lambda _r: SelfImprovementResult(packets=pkts))

    banner("STEP 1 — big goal 분해: PM brief 가 FIRST artifact + governance-required (설계강제)")
    out = sched.tick(1); print("scheduler:", out.summary)
    parent = store.get(g.id)
    print("governance_required:", gov.is_governance_required(parent), "· children:", len(parent.children))
    ev = L.replay_governance_log(parent.id, env=env)
    print("첫 governance event kind:", ev[0].kind, "(= PM brief)")

    banner("STEP 2 — /goal govern: 설계 진행 중 (PM brief incomplete → pending, fake 아님)")
    for ln in gs.govern_lines(env, parent.id): print(ln)

    banner("STEP 3 — child 활성 후 exec: design chain 미완 → specialist 실행 차단")
    cid = parent.children[0]
    store.save(transitions.apply(store.get(cid), GoalStatus.ACTIVE))
    out = GoalExecTicker(repo_root=repo, env=env).tick(2)
    print("exec:", out.summary)
    print("child execution evidence:", "execution" in [e.kind for e in store.get(cid).evidence], "(차단됨)")

    banner("STEP 4 — operator/tech-lead 가 design chain 완료(스택 2안 비교+서명) → gate 열림")
    brief = L.PMBrief(topic=parent.title, problem="self-manage", user_value="운영 자동화",
                      acceptance_criteria=("동작 확인",), success_metrics=("회귀 green",))
    meeting = L.MeetingRecord("m1", "stack", agenda=("스택 비교",), participants=(
        L.ParticipantPosition("tech-lead", "support", "선택안"),
        L.ParticipantPosition("backend-engineer", "conditional", "우려", concerns=("x",))),
        decisions=("채택",))
    stack = L.StackComparison("stack", options=(
        L.StackOption("A", pros=("단순",), cons=("제약",)),
        L.StackOption("B", pros=("확장",), cons=("복잡",))),
        recommended="A", rationale="단순 우선", tradeoffs=("확장성 포기",))
    dec = L.tech_lead_decide(brief, meeting, stack, design_system="forgekit tokens",
                             coding_convention="ruff+gitmoji", rationale="단순 우선")
    ho = L.handoff_to_engineer(dec, "backend-engineer", scope=("구현",), test_strategy="unit",
                               acceptance_criteria=("동작 확인",))
    br = L.build_specialist_briefing(brief, dec, ho)
    gov.record_artifacts(parent.id, brief=brief, meeting=meeting, decision=dec, handoff=ho,
                        briefing=br, env=env)
    for ln in gs.govern_lines(env, parent.id): print(ln)
    print("design_ready:", gov.design_ready(parent.id, env=env))
