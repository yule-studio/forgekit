# daemon ↔ autopilot execution — evidence (#241, WT2)

always-on 데몬이 **관측만** 하던 것을 넘어, 매 tick 에서 **bounded safe-class 실행**을
실제로 수행한다는 실측 증거.

| 파일 | 무엇 |
| --- | --- |
| `serve-log.txt` | 실 `forgekit runtime serve --max-ticks 2` 출력 — exec 3 / waits 2 |
| `sample-executed-note.md` | tick 이 **실제로 쓴** safe-class note 1개(실 finding 기반) |
| `heartbeat-after-serve.json` | 종료 후 heartbeat 스냅샷 |

## 무엇이 일어났나 (정직)
- tick = 관측(repo-local) → 내부 승인 chain(PM→gateway→tech-lead) → **safe-class 만** 실제 mutation
  (`BoundedMutator`, write+verify) → 기록.
- tick 1: **exec 3**(실 파일 3개 write+verify) + propose 1 — "운영/배포 준비 점검"(L4 restricted)은
  실행하지 않고 **surface 만**(waiting → operator 알림).
- tick 2: 같은 finding 은 **dedupe** → exec 0 (idempotent note 의 no-op churn 방지).

## 경계 (intentionally bounded / 정직)
- 실행 클래스 = note/docs-stub/format **만**, 경로 = `runs/`/`docs/`/`examples/` prefix **만**(`BoundedMutator`).
  **소스 코드 수정은 #240 범위**(이 데몬도 그 한도를 따른다).
- risky/restricted/blocked = 자동 실행 **안 함** → inbox+console+desktop(≥2 surface) 알림.
- **single executor invariant**: 한 tick 안에서 ExecutorArbiter 단일 슬롯(serial).
- **dedupe 는 in-session**: 프로세스 재시작 후 같은 note 는 이미 존재 → no-op → 정직히 propose/halt 로 surface
  (fake success 아님). 영속 dedupe 는 후속.
- macOS sleep 시 프로세스 suspend(상시 가동은 homeserver/systemd 1급, #243).
