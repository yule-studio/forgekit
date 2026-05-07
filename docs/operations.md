# Operations — Always-on engineering runtime

이 문서는 engineering-agent 를 상시 서비스로 운영하기 위한 가이드다. M6.0 부터는 `yule runtime up --profile engineering` 한 명령으로 worker 전체를 단일 부모 process 아래 띄울 수 있고, production 배포는 동일 entrypoint (`yule run-service <id>`) 를 systemd unit 으로 호출한다. 기존 `yule discord up` 은 dev legacy launcher 로 유지된다 (deprecate 안 함).

## 0. 빠른 시작 (M6.0)

dev / 단일 호스트:

```bash
yule runtime up --dry-run               # 띄울 service 목록 확인 (실제 spawn 없음)
yule runtime up                         # 전체 engineering runtime 부팅
yule run-service eng-research-worker    # 단일 worker (CLI 또는 systemd 가 호출)
```

production (systemd):

```bash
sudo systemctl start yule.target
sudo systemctl status yule-run-service@eng-research-worker.service
journalctl -u yule-run-service@eng-supervisor-watch.service -f
```

자세한 systemd unit / 설치 절차는 [`deploy/systemd/README.md`](../deploy/systemd/README.md).

## 1. 핵심 원칙

- **discord 연결과 작업 실행을 분리한다.** 각 worker 는 자기 큐를 polling 하고, Discord 클라이언트는 선택적 어댑터다.
- **member 서비스 = role 단위.** tech-lead / backend / qa / devops 가 각각 독립 systemd 인스턴스. 한 role 장애 ≠ engineering 전체 중단.
- **shared state = SQLite.** 모든 worker 는 SQLite 의 `job_queue` / `session.extra` / `service_heartbeats` 만 읽고 쓴다.
- **parallel-first, serial fallback.** active role 이 여러 개면 동시에, dependency 가 있을 땐 serial chain. 죽은 role 은 자동 제외.

## 2. 서비스 목록 (권장)

| 서비스 | 책임 | Discord 연결 | 큐 |
|---|---|---|---|
| `yule-eng-gateway` | `#업무-접수` 청취, intake → job 생성, status 응답 | yes | `gateway-inbox` |
| `yule-eng-member@tech-lead` | tech-lead 역할 turn / synthesis | yes | `role:tech-lead` |
| `yule-eng-member@backend-engineer` | Spring / API / DB | yes | `role:backend-engineer` |
| `yule-eng-member@qa-engineer` | 회귀 / acceptance | yes | `role:qa-engineer` |
| `yule-eng-member@devops-engineer` | CI / Docker / 배포 | yes | `role:devops-engineer` |
| `yule-eng-member@ai-engineer` | RAG / agent runtime | yes | `role:ai-engineer` |
| `yule-eng-member@frontend-engineer` | (Spring 단일팀 단계: lazy-on) | yes | `role:frontend-engineer` |
| `yule-eng-member@product-designer` | (lazy-on) | yes | `role:product-designer` |
| `yule-research-worker` | 큐 입력 시 collector 실행, `research_pack` 저장 | no | `crawl` |
| `yule-obsidian-writer` | `ready_for_obsidian` 세션 vault 적재 | no | `obsidian-write` |
| `yule-approval-worker` | `pending_approval` 세션 `#승인-대기` 처리 | yes (limited) | `approval` |
| `yule-supervisor` | watchdog: heartbeat 검사 / lease reaper | no | (read-only) |
| `yule-planning-bot` | 기존 planning 봇 | yes | `planning-inbox` |

## 3. 작업 상태 머신

```
discovered → queued → assigned → in_progress
                                   ├─ → waiting_for_role → assigned (parent done 시)
                                   ├─ → researching → in_progress
                                   ├─ → pending_approval → in_progress (사용자 답 시)
                                   ├─ → ready_for_obsidian → assigned (obsidian-writer 가)
                                   ├─ → saved (obsidian success)
                                   ├─ → failed_retryable → queued (백오프 후)
                                   └─ → failed_terminal (종단)
```

상태 머신과 큐 인프라는 `agents/job_queue/` 모듈에 구현되어 있다. 자세한 transition 규칙은 코드 + `tests/job_queue/*` 참조.

## 4. 병렬 / 직렬 실행

기본은 `parallel-first`. 직렬은 dependency / synthesis 단계에서만.

```
intake (gateway)
  └─ enqueue role:tech-lead "frame this task"            [SERIAL #1]
        └─ tech-lead 가 active_research_roles 결정 후
           → enqueue role:backend / qa / devops "review" [PARALLEL]
              └─ 각자 독립으로 _collect_role_research_pack + role take
        └─ N 개가 다 끝나면 (or budget exhausted)
           → enqueue role:tech-lead "synthesis"          [SERIAL #2]
              └─ work_report 생성 + Phase 6 게이트 통과 시 ready_for_obsidian
```

직렬 vs 병렬은 `JobQueue.enqueue(job, after_jobs=[parent_id])` (dependency edge) / `JobQueue.enqueue_fanout(jobs)` (동시 dispatch) 로 표현한다.

## 5. degrade 규칙

- role worker 가 N 분 heartbeat 없거나, dispatch 후 timeout (env: `ENGINEERING_ROLE_TURN_TIMEOUT_SECONDS=180`) → supervisor 가 그 job 을 `failed_retryable` 로 표시 → retry count 까지 재dispatch → 재시도 다 실패하면 `failed_terminal`.
- 한 role 이 `failed_terminal` 이어도 fanout 의 다른 role 은 진행. tech-lead synthesis 단계에서 missing role 이 있으면 work_report 가 자동으로 `interim` 으로 떨어짐 (Phase 6 가 처리).
- 모든 active role 이 `failed_terminal` 이면 gateway worker 가 fallback 으로 deterministic role take 를 직접 생성. 사용자에겐 "X 역할 봇 응답 불가, gateway 가 대체 take 작성" 이라고 명시.
- member bot 토큰이 없으면 systemd unit 이 `ConditionEnvironment=ENGINEERING_AGENT_BOT_<ROLE>_TOKEN` 으로 비활성화. supervisor 가 "비활성 role" 로 분류 후 gateway 가 active_research_roles 에서 자동 제외.

## 6. 장애 복구

1. **Lease 만료 reaper** — supervisor 가 5 초마다 `WHERE picked_until < now AND state='in_progress'` 잡아서 `failed_retryable` 로 되돌림. attempts++.
2. **heartbeat watchdog** — worker 는 30 초마다 `service_heartbeats` upsert. supervisor 는 90 초 미박동 → systemd `Restart=on-failure` cleanup. supervisor 자신은 `Restart=always`.
3. **Discord 인증 실패 처리** — member worker 가 Discord disconnect / 4401 받으면 stderr 에 명확한 에러 + exit code 78. systemd 가 재기동 안 하도록 `RestartPreventExitStatus=78` (잘못된 토큰을 무한 재시작하지 않음).
4. **graceful shutdown** — SIGTERM 받으면 worker 는 (a) 새 job 픽 정지, (b) 진행 중 job 의 `picked_until` 을 5 초 후로 단축, (c) Discord 클라이언트 close 후 exit.
5. **재시작 복구** — cold start 시 worker 는 `state='in_progress' AND picked_by=self` 로 자기 lease 가 남은 job 을 우선 회수. process 가 SIGKILL 됐으면 lease 만료 후 다른 인스턴스가 회수.
6. **fallback 합성** — 모든 role 이 `failed_terminal` 이어도 gateway worker 가 deterministic fallback 으로 role take 생성 (현재 `_deterministic_role_take` 활용).
7. **`#봇-상태` 채널** — supervisor 가 매시 정각 heartbeat 요약 게시. 죽은 service 가 있으면 즉시 알림.

## 7. systemd 배치

### 7-1. 디렉토리

```
deploy/systemd/
├── yule.target                      # 종합 target
├── yule-eng-gateway.service
├── yule-eng-member@.service         # template, %i 가 role 이름
├── yule-research-worker.service
├── yule-obsidian-writer.service
├── yule-approval-worker.service
├── yule-supervisor.service
├── yule-planning-bot.service
└── yule-env.conf                    # EnvironmentFile, .env.local 과 분리된 운영 env
```

### 7-2. template unit 예 (`yule-eng-member@.service`)

```ini
[Unit]
Description=Yule engineering member bot (%i)
After=network-online.target
PartOf=yule.target
Wants=yule-supervisor.service

[Service]
Type=simple
User=yule
WorkingDirectory=/opt/yule-studio-agent
EnvironmentFile=/etc/yule/yule-env.conf
EnvironmentFile=-/etc/yule/yule-env.%i.conf
ExecStart=/opt/yule-studio-agent/.venv/bin/yule discord member --role %i --as-service
Restart=on-failure
RestartSec=10s
RestartPreventExitStatus=78
StartLimitInterval=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=yule.target
```

### 7-3. 운영 명령

```bash
systemctl start yule.target              # 일괄 기동
systemctl restart yule-eng-member@qa-engineer
systemctl status yule-eng-gateway
journalctl -u yule-eng-member@backend-engineer -f
```

### 7-4. macOS 개발자용 (옵션)

`launchd` 의 `KeepAlive` + `ThrottleInterval` 로 동등한 single-instance 정의. 또는 `tmux` 세션 기반 dev 스크립트. 단일 머신 dev 는 `yule discord up` 으로 충분하다.

## 8. 마이그레이션 단계

| 단계 | 범위 | 검증 |
|---|---|---|
| **M1. 큐 인프라** | `agents/job_queue/` 모듈 + 테이블 + lease + state machine | 큐 단위 테스트 |
| **M2. heartbeat / supervisor watch** | `service_heartbeats` + `yule supervisor --watch` | watchdog 90s 이내 detect 단위 테스트 |
| **M3. research worker 분리** | `_run_engineering_research_loop` → `enqueue('research_collect')` | 라이브 시나리오 1 (k8s) 동일 결과 |
| **M4. role worker 큐 변환** | `handle_research_turn_message` 가 큐 컨슈머에서 호출 | 라이브 시나리오 1·2 변경 없음 |
| **M5. obsidian-writer / approval-worker 분리** | `ready_for_obsidian` 도달 세션을 별 worker 가 픽 | 라이브 시나리오 4 동일 결과 |
| **M6. systemd unit** | `deploy/systemd/` 추가, `yule discord up` 은 dev-only docstring | 1 role 만 재기동 시 다른 role 무영향 |
| **M7. fallback / degrade** | role timeout / failed_terminal 시 gateway deterministic fallback | 의도적 1 role 다운 라이브 회귀 |
| **M8. cleanup** | dev-only `discord up` 를 `--dev` 플래그 뒤로 hide, 운영 README/policy 갱신 | — |

각 단계 종료 시 `1561+α` 자동 테스트 + `policies/runtime/agents/engineering-agent/live-regression.md` 4 시나리오 통과를 게이트로 둔다.

---

## 9. M7-final 운영 가이드 — fallback / degrade / circuit / status posting

A-M7 계열이 모두 닫힌 뒤 운영자가 알아야 할 항목을 한 자리에 모아 둔다.

### 9-1. degrade / fallback 자동화

- standalone synthesis runner (production: `yule run-service eng-role-tech-lead`) 와 in-process gateway (`yule discord up`) 모두 동일한 트리거를 사용한다 — `runtime/fallback.scan_role_take_results` 가 SAVED / FAILED_TERMINAL / FAILED_RETRYABLE / 누락 으로 role 단위 분류한다.
- 일부 role 만 실패 → tech-lead synthesis 본문 위에 `[degrade] 실패한 역할: …` 배너가 자동으로 prepend 되고 `session.extra['fallback_audits']` 에 `degraded_synthesis` audit 이 기록된다 (`human_approval_required=False`).
- 모든 active role 실패 → deterministic template synthesis 가 생성되고 "fallback 으로 생성됨" 헤더 + `승인 필요: yes` 가 표시된다. audit authority 는 `deterministic_template`, `human_approval_required=True`. M5b ObsidianWriterWorker 의 approval guard 가 자동 vault 저장을 차단하므로 사람이 명시 승인하지 않으면 final knowledge 로 굳지 않는다.
- 한 role 이 `FAILED_RETRYABLE` 로 남아 있으면 (retry 가능) terminal fallback 으로 성급히 넘어가지 않는다.

### 9-2. circuit-break + persist + reset

- supervisor parent 가 5 분 안 5 회 restart 가 발생한 service 의 breaker 를 open 처리한다 (in-memory 정책). open 된 행은 SQLite `circuit_breaker_state` 테이블에 mirror 되어 sibling process (status CLI / status poster) 도 즉시 인지한다.
- `yule runtime status [--profile engineering]` 의 텍스트 / JSON 출력에 `CIRCUIT_OPEN` 상태가 표시되고, warnings 섹션에 reset 명령 힌트가 함께 출력된다.
- 운영자 reset:

  ```bash
  yule runtime circuit reset eng-role-backend-engineer
  yule runtime circuit reset eng-role-backend-engineer --json   # 자동화용
  ```

  - 이미 open 이 아닌 service id 에도 안전하게 동작 (idempotent).
  - inventory 에 없는 id → exit 78 (`EX_CONFIG`) 와 명확한 에러.

### 9-3. `#봇-상태` 주기 posting

- supervisor watch loop (`eng-supervisor-watch`) 가 환경 변수로 활성화되면 일정 간격마다 status 요약을 `#봇-상태` 에 게시한다. 기본값은 안전하게 disabled.

  | 환경 변수 | 기본값 | 의미 |
  |---|---|---|
  | `ENGINEERING_STATUS_POST_ENABLED` | `false` | `true` / `1` / `yes` / `on` 으로 활성화 |
  | `ENGINEERING_STATUS_POST_INTERVAL_SECONDS` | `3600` | 최소 60 초 까지 강제. 이보다 작으면 60 초로 clamp |
  | `DISCORD_ENGINEERING_STATUS_CHANNEL_ID` | (unset) | 우선 사용. 없으면 NAME fallback |
  | `DISCORD_ENGINEERING_STATUS_CHANNEL_NAME` | (unset) | `DISCORD_GUILD_ID` 와 함께 설정 시 REST 로 조회 |

- 게시 본문은 `runtime/status_summary.render_status_summary_markdown` 출력. 동일한 dedup key 가 이전 게시와 같으면 자동 skip 되므로 clean 상태가 반복 게시되지 않는다.
- post 실패 (401/403/404/429/timeout 등) 는 supervisor 를 죽이지 않고 warning 로그로만 남는다. 토큰은 어떤 에러 상수에도 포함되지 않는다.
- 수동 게시는 여전히 `yule runtime status --profile engineering --post-discord [--force-post]`.

### 9-4. fallback / degrade 즉시 알림

- 전용 트리거 채널 / 별도 Discord 호출 없이 dedup key 만으로 동작한다. fallback audit 의 `fallback_id` 가 dedup hash 에 포함되므로, 새 fallback / 새 degrade 가 발생하면 다음 supervisor tick (또는 운영자의 수동 `--post-discord`) 에서 자동으로 #봇-상태 가 갱신된다.
- "더 빠르게 알림" 이 필요하면 `ENGINEERING_STATUS_POST_INTERVAL_SECONDS` 를 짧게 (예: 60–300 초) 잡으면 된다. dedup 이 clean 상태 반복 게시를 막아 주므로 짧은 interval 이 곧 spam 으로 이어지지 않는다.

### 9-5. standalone vs legacy

- 권장 운영 경로 = systemd + `yule run-service ...` (M6.0 이후).
- `yule discord up` 은 dev / 단일 호스트 편의용으로 유지된다 (deprecate 안 함). M7-final 부터는 in-process synthesis 도 standalone helper 로 위임되어 두 경로의 degrade / fallback 동작이 일치한다.

### 9-6. 라이브 검증

- 실제 Discord live posting 검증은 사용자 명시 승인이 있을 때만 수행한다. 자동화된 단위 / 통합 테스트는 stub HTTP 로 동일 분기를 모두 검증하므로 코드 변경 자체로는 라이브 호출이 필요하지 않다.

