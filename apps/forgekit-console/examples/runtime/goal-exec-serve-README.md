# goal → always-on 물리 실행 — evidence (G1, GW4-B)

`apply_approved_packet` 에 **호출자가 없어서** operator 가 `/goal` 로 승인한 packet 이
실제로는 한 번도 물리 실행되지 않던 seam(G1)을 닫은 실측 증거.

이제 always-on 런타임(`forgekit runtime serve`)이 매 tick 에서 **ACTIVE(operator-승인)
goal** 을 읽어 그 linked safe-class packet 을 `BoundedMutator` 로 **실제로 실행**한다.

| 파일 | 무엇 |
| --- | --- |
| `goal-exec-serve.txt` | 실 serve tick(goal-exec pass) 출력 — ACTIVE safe goal 1개 실행 + 실 commit, risky/awaiting skip, tick2 idempotent |

## operator 경로 (정직)
1. operator 가 `/goal` 로 goal 을 ACTIVE 로 승인(기존, merge 완료) — **ACTIVE 상태 자체가
   "진행해도 좋다"는 operator 승인**.
2. `forgekit runtime serve` 의 tick(`_build_tick_fn` → autopilot pass + **goal-exec pass**)이
   매 tick 마다 ACTIVE goal 의 linked packet 을 `apply_approved_packet` 로 실행:
   - **safe-class + 3-gate 통과** → 실제 bounded write(`runs/…`) + 실 `git -C` commit(**push 안 함**)
     + `execution`/`verification` evidence 를 goal 에 기록.
   - **risky/destructive/미인가** → 실행 안 함, `decision` 거부 evidence 만 기록(gated-record).
3. operator 가 `forgekit runtime status` / goal evidence 로 결과 확인.

## 무엇이 물리 실행 vs gated-record (정직)
- **물리 실행:** safe-class + (`run_internal_chain`+`can_specialist_execute`
  +`authorize_runtime_execution`+`validate_execution`) 3-gate PASS 분만 → BoundedMutator
  재읽기 검증 후 실 commit.
- **gated-record(실행 안 함):** risky(L3)/destructive(L4 deploy·secret)/내부 미승인 →
  `decision` 거부 evidence 만. fake "executed" 없음.

## 경계 (intentionally bounded)
- **ACTIVE goal 만** — awaiting_approval/blocked/draft/done 은 skip.
- **bounded per tick** — 최대 2 goal × 2 packet/goal(churn 방지).
- **idempotent** — 이미 `execution`(실행) 또는 `decision`(거부) evidence 가 있는 packet 은
  다음 tick 에 재시도 안 함 → 중복 commit / 중복 거부 기록 없음.
- **never push / never destructive / never auto-`done`** — 물리 mutation 은 caps+verify
  실패 시 rollback(no commit). goal 을 `done` 으로 올리지 않음(별도 게이트 결정).
- macOS sleep 시 프로세스 suspend(상시 가동은 homeserver/systemd 1급).
