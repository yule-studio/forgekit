# goal-driven execution continuity (G1) — evidence

`goal-continuity-evidence.txt` 는 always-on serve 루프가 **장기 goal 을 실제로 계속 굴리는지**를
deterministic(temp git repo + temp goal store)으로 캡처한다. SSoT: `docs/forgekit-goal-roadmap.md`
(GW4-B 서브). 코드 `packages/forgekit-runtime/src/forgekit_runtime/runtime/goal_exec_tick.py`
+ `autopilot_tick.py`(serve tick 배선), 회귀 `tests/forgekit/test_goal_exec_tick.py`.

핵심(= host uptime 이 아니라 goal-driven execution continuity):
1. **serve tick → ACTIVE goal 의 다음 safe packet 물리 실행** — `apply_approved_packet`(BoundedMutator
   + 실제 git commit + evidence goal store & vault). STEP 2 의 REAL commit + execution/verification 증거.
2. **정직 경계** — risky/destructive proposal 은 goal 을 `awaiting_approval` 로 두고 **자동 실행 안 함**;
   operator `/goal approve`(gw3) 전까지 대기(STEP 1/4). 승인 없는 risky 는 절대 auto-run 없음(no fake).
3. **continuity + dedupe** — 다음 tick 은 이미 실행한 packet 을 재실행하지 않고(STEP 3), goal 이
   살아있는 한 다음 safe step 을 계속 찾아 진행. bounded(틱당 goal/packet 상한) · stoppable(kill-switch)
   · inspectable(evidence + TickOutcome).

이전엔 `apply_approved_packet` 호출자가 0(seam) — goal tick 은 packet/evidence 만 만들고 멈췄다.
G1 이 serve 루프(AutopilotTicker)에서 그 실행 경로를 배선해 경계를 닫았다.

재생성:
```
PYTHONPATH=<packages/*/src> python3 \
  apps/forgekit-console/examples/goal-continuity/_regen.py \
  > apps/forgekit-console/examples/goal-continuity/goal-continuity-evidence.txt
```
