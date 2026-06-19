# WT4 always-on-runtime — operator guide + evidence

forgekit 가 Claude Code 세션이 아니라 **자체 long-running bounded 데몬**으로 돈다.
코드 SSoT: `runtime/daemon.py`(serve loop) · `runtime/heartbeat.py`(상태/kill) · `cli/runtime_cmd.py`.

## 명령 (CLI)
```
forgekit runtime serve [--interval 300] [--max-ticks 0] [--repo-root P]   # 장시간 loop 시작
forgekit runtime once                                                     # 단일 tick
forgekit runtime status                                                   # heartbeat 상태
forgekit runtime stop                                                     # kill switch (다음 tick 종료)
```

## 콘솔 surface (TUI, WT3)
CLI 와 같은 heartbeat 파일을 콘솔에서도 본다 — `runtime/surface.py` → `/daemon` 핸들러.
```
/daemon          # state(alive/stopped/kill-pending) · status · tick · last_tick · pid · kill-switch
/daemon stop     # kill-switch SET — 실행 중 serve 는 다음 tick 에 정지
```
`/daemon` 은 `forgekit runtime status` 와 **동일한** `runtime-heartbeat.json` 을 읽는다(별도 상태 아님).
heartbeat 파일이 없으면 정직하게 `stopped` + 시작 hint 를 표시하고, 가짜 liveness 를 만들지 않는다.

## 무엇이 bounded autonomy 인가 (정직)
- 각 tick = **관측(repo-local) → 내부 승인 chain(PM→gateway→tech-lead) → safe-class 실제 실행
  (BoundedMutator, write+verify) → 기록**(#241, `runtime/autopilot_tick.py`). 관측-only 가 아니다 —
  실측: `daemon-execution/`.
- 실행 클래스 = note/docs-stub/format + `runs/`·`docs/`·`examples/` prefix **만**(소스 코드 수정은 #240).
  cross-tick **dedupe**(no-op churn 방지) + 반복 verify 실패 시 **cooldown**.
- privileged(deploy/secret/infra) / risky / restricted 는 **runbook + approval-wait** — 데몬이 자동 실행 안 함, surface 만.
- 매 tick **heartbeat** 기록(`$FORGEKIT_HOME/state/runtime-heartbeat.json`), approval-needed → **operator 알림**
  (inbox 항상 + desktop `FORGEKIT_NOTIFY` opt-in, 2 surface).
- 종료: kill switch 파일 / SIGTERM·SIGINT / `--max-ticks`.

## 운영 경로
- **Linux / homeserver / OCI = 1급 경로** — `forgekit-runtime.service`(systemd user unit) 참조. 상시 가동.
- **macOS = launchd 지원**(`com.forgekit.runtime.plist`) — **단, 정직한 한계**: 노트북 **덮개 닫힘 + sleep** 이면
  프로세스가 **suspend** 된다(깨어나면 재개). "덮어도 계속 돈다"는 **불가** — 상시 가동은 홈서버/Linux 권장.
  (caffeinate 로 sleep 억제는 가능하나 배터리/발열 — 권장 안 함.)

## 실측 evidence (serve --max-ticks 3 --interval 0)
```
stopped: max_ticks(3) · ticks 3 · waits 3 · notified 3
status: stopped · tick 3 · ts 2026-06-18T... · pid ...
```
`heartbeat-sample.json` = serve 가 남긴 실제 heartbeat. tick 마다 갱신.
