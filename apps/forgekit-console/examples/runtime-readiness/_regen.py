"""Regenerate runtime-readiness-evidence.txt — deterministic (tempdir).
always-on 진행 가능성을 daemon × goal continuity × declared live transport 로 join 한
honest readiness verdict. 재현: tests/forgekit/test_runtime_readiness.py
"""
from __future__ import annotations
import tempfile
from forgekit_goal import Goal, GoalStatus, GoalStore
from forgekit_runtime.runtime import readiness as R

FOUR = {"primary_provider": "claude",
        "linked_providers": ["claude", "codex", "gemini", "ollama"],
        "slot_routing": {"default_chat": "gemini", "execution": "codex", "research": "gemini"}}
NO_LIVE = {"primary_provider": "claude", "linked_providers": ["claude", "codex"],
           "slot_routing": {"default_chat": "claude", "execution": "codex", "research": "claude"}}

def banner(t): print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)

print("ForgeKit runtime readiness — daemon × goal × live transport join (operator-first) — deterministic evidence")
print("재현: tests/forgekit/test_runtime_readiness.py · 하드레일: no fake-live, platform-honest unattended")
with tempfile.TemporaryDirectory() as home:
    env = {"FORGEKIT_HOME": home}; store = GoalStore(env=env)
    banner("STEP 1 — provider 미설정: verdict=setup_required (정직)")
    for ln in R.readiness_lines(env=env, config={}, store=store): print(ln)
    banner("STEP 2 — four-brain, goal 없음: verdict=idle_no_goals · transport 미검증(probe 안 함)")
    for ln in R.readiness_lines(env=env, config=FOUR, store=store): print(ln)
    store.save(Goal(id="g1", title="big goal", intent="x", status=GoalStatus.ACTIVE))
    banner("STEP 3 — active goal + four-brain + probe(gemini live): verdict=progressing · transport 검증됨")
    for ln in R.readiness_lines(env=env, config=FOUR, store=store, live_map={"gemini": True, "ollama": False}): print(ln)
    banner("STEP 4 — active goal 이지만 claude/codex(CLI)만: verdict=no_live_lane (packet 누적, fake-live 아님)")
    for ln in R.readiness_lines(env=env, config=NO_LIVE, store=store): print(ln)
    store.save(Goal(id="g1", title="big goal", intent="x", status=GoalStatus.AWAITING_APPROVAL))
    banner("STEP 5 — awaiting_approval: verdict=awaiting_operator (operator 행동 필요)")
    for ln in R.readiness_lines(env=env, config=FOUR, store=store): print(ln)
    banner("STEP 6 — machine-readable readiness")
    print(R.assess_runtime_readiness(env=env, config=FOUR, store=store).to_dict())
