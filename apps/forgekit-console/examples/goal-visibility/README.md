# always-on runtime — goal-continuity 가시성 (evidence)

`goal-visibility-evidence.txt` 는 `forgekit runtime status`/`/daemon` 이 goal-driven continuity 를
operator-visible 하게 표면하는지 deterministic(tempdir goal store)으로 캡처. Issue #372. 코드
`packages/forgekit-runtime/src/forgekit_runtime/runtime/goal_status.py` + `surface.py`,
회귀 `tests/forgekit/test_goal_status_visibility.py`.

증명:
1. **goal 없음** → 정직 표기(`활성 goal 없음`), fake 진행 없음.
2. **active(진행) + awaiting(승인 필요)** → runtime status 에 `goal-loop : active N · awaiting N ·
   blocked · done` + **action-needed**(`/goal approve <id>`) + **last work**(실제 execution/verification
   evidence) 표면. serve 가 자동 진행하는 active goal 과 operator 승인 대기 goal 이 한 화면에서 보임.
3. store 없음/읽기 불가 → 정직 "없음"(no fake).

재생성:
```
PYTHONPATH=<packages/*/src> python3 \
  apps/forgekit-console/examples/goal-visibility/_regen.py \
  > apps/forgekit-console/examples/goal-visibility/goal-visibility-evidence.txt
```
