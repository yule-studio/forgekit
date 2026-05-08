# Operations — Always-on engineering runtime

이 문서는 engineering-agent 를 상시 서비스로 운영하기 위한 가이드다. **M8 이후 운영 1급 경로는 `yule runtime up` / `yule run-service` / `yule runtime status` 세 가지뿐이다.** `yule discord up` 은 dev/test launcher 로만 남고, **queue 워커를 띄우지 않으므로 단독으로는 실제 작업을 처리할 수 없다.** 운영자가 어떤 프로세스를 켜야 queue 가 실제 처리되는지 헷갈리지 않게 — production 경로와 dev 경로를 한눈에 구분할 수 있는 표를 §0.1 에 둔다.

## 0. 빠른 시작 (M8)

dev / 단일 호스트:

```bash
yule runtime up --dry-run               # 띄울 service 목록 확인 (실제 spawn 없음)
yule runtime up                         # 전체 engineering runtime 부팅 (12 services)
yule runtime status                     # 헬스 + 큐 + 실패 + 라이브 스모크 체크리스트
yule run-service eng-research-worker    # 단일 worker (systemd 도 같은 명령을 호출)
```

production (systemd):

```bash
sudo systemctl start yule.target
sudo systemctl status yule-run-service@eng-research-worker.service
journalctl -u yule-run-service@eng-supervisor-watch.service -f
```

자세한 systemd unit / 설치 절차는 [`deploy/systemd/README.md`](../deploy/systemd/README.md).

### 0.1. 운영 경로 vs. dev 경로

| | `yule runtime up` (production) | `yule discord up` (dev only) |
|---|---|---|
| Discord 봇 spawn | ✅ gateway + 7 멤버 | ✅ gateway + 7 멤버 + planning |
| queue 워커 spawn | ✅ research / role / approval / obsidian-writer / supervisor | ❌ 없음 — gateway 가 enqueue 한 job 은 unpicked |
| 실제 작업 처리 | ✅ end-to-end | ❌ Discord 발화는 보이지만 결과가 안 나온다 |
| systemd 동등 | `yule.target` (각 서비스 = `yule-run-service@<id>.service`) | 없음 |
| 사용 시점 | 항상 (단일 호스트 / production) | Discord 발화만 빠르게 보고 싶은 dev smoke |

**operator 결정 트리:**

- 작업이 실제로 처리돼야 한다 → `yule runtime up` (또는 systemd `yule.target`).
- Discord 발화만 확인하고 queue 는 신경 안 써도 된다 → `yule discord up`.
- 단일 worker 만 띄우고 싶다 → `yule run-service <service-id>` (또는 `systemctl start yule-run-service@<id>.service`).

### 0.2. 서비스 한눈에 보기

`yule runtime up` (engineering profile) 이 띄우는 12 개 서비스 — 각 행이 어떤 job 을 처리하는지:

| service id | kind | 처리하는 큐 / 작업 |
|---|---|---|
| `eng-supervisor-watch` | supervisor | 큐 컨슈머 아님. heartbeat sweep + lease reaper. |
| `eng-research-worker` | research_worker | `research_collect` 큐 — auto_collect → research_pack 적재. |
| `eng-role-tech-lead` | role_worker | `role_take` 큐 (role=tech-lead). |
| `eng-role-backend-engineer` | role_worker | `role_take` 큐 (role=backend-engineer). |
| `eng-role-qa-engineer` | role_worker | `role_take` 큐 (role=qa-engineer). |
| `eng-role-devops-engineer` | role_worker | `role_take` 큐 (role=devops-engineer). |
| `eng-role-ai-engineer` | role_worker | `role_take` 큐 (role=ai-engineer). |
| `eng-role-frontend-engineer` | role_worker | `role_take` 큐 (role=frontend-engineer). |
| `eng-role-product-designer` | role_worker | `role_take` 큐 (role=product-designer). |
| `eng-approval-worker` | approval_worker | `approval_post` 큐 — `#승인-대기` 카드 게시 + 답신 인입. |
| `eng-obsidian-writer` | obsidian_writer | `obsidian_write` 큐 — vault 저장 (approval guard 적용). |
| `eng-discord-gateway` | discord_gateway | 큐 컨슈머 아님. `#업무-접수` listener — research/role/approval/obsidian_write 큐에 enqueue. |

`yule runtime status` 의 ALIVE/STALE/UNKNOWN 라벨 + warnings 섹션이 위 표를 그대로 따른다. STALE/UNKNOWN 이 뜨면 status warnings 가 정확한 복구 명령을 함께 출력한다 (`yule run-service <id>` / `systemctl restart …` / `yule runtime up`).

### 0.3. `yule runtime status` 의 라이브 스모크 체크리스트

`yule runtime status` 출력 끝에 6-step 라이브 스모크 체크리스트가 항상 포함된다. operator 가 한 화면에서 다음 명령을 그대로 복사해 검증할 수 있다 — `dry-run` → `up` → `status` → `#업무-접수 인입` → `#승인-대기 답신` → 의도적 worker kill 회귀.

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

---

## 10. M7.5 운영-리서치 토의 + Obsidian handoff 라이브 검증

A-M7.5 / A-M7.5b 가 닫은 forum 토의 → 역할 추가 → Obsidian 저장 요청 → `#승인-대기` 카드 → 사용자 승인 → vault write 흐름을 실제 Discord 표면에서 검증하는 운영자 가이드. 자동화 unit / production-path smoke 는 모두 green (A-M7.5c). 라이브 단계는 사용자가 직접 메시지를 입력하는 절차다.

자세한 단계별 시나리오 + 검증 항목은 [policies/runtime/agents/engineering-agent/live-regression.md §6](../policies/runtime/agents/engineering-agent/live-regression.md) 참고. 여기서는 **사전 환경 설정 + 운영자 체크리스트** 만 정리한다.

### 10-1. 필수 env (`.env.local`)

| 키 | 값 예시 | 의미 |
|---|---|---|
| `DISCORD_GUILD_ID` | `<guild id>` | 운영 길드 |
| `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN` | `<bot token>` | 게이트웨이 봇 토큰. 미설정 시 `DISCORD_BOT_TOKEN` fallback |
| `DISCORD_ENGINEERING_INTAKE_CHANNEL_ID` | `<channel id>` | `#업무-접수` (intake) |
| `DISCORD_AGENT_RESEARCH_FORUM_CHANNEL_ID` | `<forum id>` | `#운영-리서치` (forum) |
| **`DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID`** | **`<channel id>`** | **`#승인-대기` — M7.5 라이브 검증 필수.** 미설정 시 NAME fallback (아래) 사용 |
| `DISCORD_ENGINEERING_APPROVAL_CHANNEL_NAME` | `승인-대기` | NAME fallback (`DISCORD_GUILD_ID` 와 함께 REST GET 으로 자동 해석) |
| `DISCORD_ENGINEERING_STATUS_CHANNEL_ID` | `<channel id>` | `#봇-상태` — M7.1 status posting 활성 시 |
| `DISCORD_ENGINEERING_STATUS_CHANNEL_NAME` | `봇-상태` | status NAME fallback |
| `OBSIDIAN_VAULT_PATH` | `<vault path>` | vault write 대상. 라이브 전에는 임시 vault 권장 (10-3 참고) |

`.env.example` 의 같은 키들은 기본 commented-out — A-M7.5 부터는 approval / status 둘 다 런타임 활성이라는 라벨로 갱신되어 있다.

### 10-2. 봇 권한 (Discord Server Settings)

게이트웨이 봇 + 7 개 멤버 봇이 다음 권한을 보유해야 한다:

| 채널 | 권한 |
|---|---|
| `#업무-접수` | 게이트웨이 봇 — `View Channel` / `Send Messages` / `Read Message History` |
| `#운영-리서치` (Forum) | 게이트웨이 + 멤버 봇 모두 — 위 + `Send Messages in Threads` / `Create Public Threads` |
| `#승인-대기` | 게이트웨이 봇 — `View Channel` / `Send Messages` / `Read Message History`. 답신 라우팅에 message-content intent 필요 |
| `#봇-상태` | 게이트웨이 봇 — `Send Messages` (status posting 활성 시) |

Discord Developer Portal 에서 각 봇 앱마다 **Message Content Intent** 가 켜져 있어야 한다 (런타임 검증 불가 — Portal 토글).

### 10-3. 라이브 검증 전 체크리스트

라이브 시나리오 시작 전에 한 번에 확인:

- [ ] `.env.local` 의 `DISCORD_ENGINEERING_APPROVAL_CHANNEL_ID` 또는 `_NAME` 활성 (uncomment + 값 채움).
- [ ] `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN` (또는 `DISCORD_BOT_TOKEN`) 활성.
- [ ] 게이트웨이 봇이 `#승인-대기` 채널에 `Send Messages` 권한 보유 (Discord Server Settings).
- [ ] 게이트웨이 + 멤버 봇이 `#운영-리서치` forum 안 thread 에 `Send Messages in Threads` 권한 보유.
- [ ] 게이트웨이 봇이 `#승인-대기` 답신 메시지를 받기 위해 Message Content Intent 활성 (Developer Portal).
- [ ] **vault dry-run 권장** — `OBSIDIAN_VAULT_PATH` 를 실서비스 vault 가 아닌 임시 디렉토리로 잠시 redirect. 시나리오 종료 후 원복.
- [ ] **승인 전 vault write 금지 확인** — `agents/job_queue/obsidian_writer_worker.py:_APPROVAL_REQUIRED_KINDS` 가 `note_kind=knowledge` 또는 `overwrite=True` 에 대해 approval triple (`approval_id` / `approved_by` / `approved_at`) 없으면 `failed_retryable` 로 반려한다. 코드 변경하지 말 것 — 정책이다.
- [ ] **승인 후 obsidian_write job 생성 확인 명령 준비** — `yule runtime status` 로 `obsidian_write` job_type 의 queued/saved 카운트가 +1 되는지 본다.
- [ ] 자동화 baseline — `python3 -m unittest discover -s tests -t .` 모두 green. 직전 commit 해시 기록 (라이브 회귀 보고용).

### 10-4. 라이브 smoke 플레이북 (8 단계)

순서를 그대로 따른다. 각 단계 사이 5–10 초 간격으로 Discord rate-limit / 큐 처리를 안전하게 흡수.

1. `#업무-접수` 채널에 intake prompt 입력 — 예: `DevOps 엔지니어가 되려면 어떻게 공부해야 할까`. 게이트웨이가 작업 thread 생성 + `#운영-리서치` forum thread 생성.
2. `#운영-리서치` 에 새 thread 가 만들어졌는지 확인. starter 본문 + kickoff 의 `참여 역할 / 대기 역할 / 추가 안내 / 다음 단계` routing summary 4 라인 가시성.
3. 활성 역할만 thread 댓글을 남기는지 관찰 (개입 없음). excluded 역할 (예: frontend-engineer / qa-engineer) 의 봇은 발화 X.
4. thread 안에 `Obsidian에 정리하고 싶어` 입력. 봇 응답 `📨 Obsidian 저장 요청을 받았어요…` + `#승인-대기` 채널에 카드 등장.
5. `#승인-대기` 카드 본문 확인 — thread 제목 / source thread id / decision_id 노출. `yule runtime status` 의 `approval_post` 카운트 +1.
6. `#승인-대기` 카드에 `이대로 저장` 또는 `승인` 답신. 봇 응답 `✅ 승인 받았어요…` + `yule runtime status` 의 `obsidian_write` 카운트 +1.
7. obsidian-writer 워커가 자동 처리 → vault 디렉토리에 새 markdown 파일 등장. 파일 metadata 에 `approval_id` / `approved_by` / `approved_at` 포함.
8. `/engineer_show <session_id>` 또는 `yule engineer show <session_id> --json` 으로 `extra.obsidian_writes` 항목 추가 / `extra.fallback_audits` 변동 없음 확인.

각 단계의 자세한 검증 항목 + 회귀 처리 절차는 [live-regression.md §6.2 / §6.4](../policies/runtime/agents/engineering-agent/live-regression.md#62-검증-항목-체크리스트).

### 10-5. 사용자 직접 액션 요약

다음 항목은 **운영자/사용자** 가 직접 수행해야 하는 부분이다 (자동화 불가):

1. `.env.local` uncomment / 값 채움 (10-1 표).
2. Discord 채널 권한 부여 (10-2 표).
3. Developer Portal 의 Message Content Intent 토글.
4. `OBSIDIAN_VAULT_PATH` 를 임시 vault 로 redirect (10-3 권장).
5. `#업무-접수` 부터 답신 승인까지 8 단계 입력 (10-4 플레이북).
6. 결과를 [live-regression.md §5 리포트 양식](../policies/runtime/agents/engineering-agent/live-regression.md#5-리포트-양식) 의 시나리오 5 라인에 기록.

자동화 측면은 모두 통과 (2,182 tests green); 위 사용자 액션이 끝나면 M7.5 라이브 검증이 닫힌다.

## 11. P0 Secret Hygiene + Token Rotation

> **이 섹션은 코드 변경보다 항상 먼저 실행한다.** 노출된 토큰을 가진 채로 다음 commit 을 만들거나 라이브 검증을 시작하지 않는다.

### 11.1 언제 rotate 하는가

다음 트리거 중 **하나라도** 일치하면 해당 토큰은 즉시 무효로 간주한다.

- 스크린샷 / 화면 공유 / 영상에 `Bot ` prefix 또는 토큰 hex 첫 몇 글자가 노출됨.
- 터미널 / `journalctl` / `tail -f` / 봇이 보낸 Discord 메시지에 토큰 문자열 그대로 출력됨.
- 외부 채팅(Slack / 메일 / 이슈) 또는 외부 LLM / pair-programming 도구 컨텍스트에 토큰이 흘러감.
- git history / commit message / 공개 PR diff / 공개 fork / `.env.example` 에 실제 값이 들어감.
- `.env.local` 파일이 .gitignore 밖으로 나가거나, 백업 / Dropbox / iCloud / 사진앨범 등 원치 않는 저장소에 사본이 남음.
- Discord Developer Portal 의 token regenerate 페이지가 열린 흔적은 있는데 사용 흔적이 없음(누군가 reset 시도 가능성).

### 11.2 토큰별 Rotation 체크리스트

각 봇은 **별도 Discord application** 이다. 한 봇만 rotate 해도 되지만, 같은 노출 경로를 탔다면 9 개 전부를 점검 대상으로 본다. 토큰 값은 어떤 단계에서도 화면 / 채팅 / 로그에 그대로 출력하지 않는다 — 항상 secret manager / `.env.local` 직접 편집으로만 다룬다.

#### 11.2.1 Gateway / 멤버 봇 (8 종)

| 봇 | env key | Discord app |
|---|---|---|
| Engineering gateway | `ENGINEERING_AGENT_BOT_GATEWAY_TOKEN` | engineering-gateway |
| tech-lead | `ENGINEERING_AGENT_BOT_TECH_LEAD_TOKEN` | engineering-tech-lead |
| ai-engineer | `ENGINEERING_AGENT_BOT_AI_ENGINEER_TOKEN` | engineering-ai-engineer |
| product-designer | `ENGINEERING_AGENT_BOT_PRODUCT_DESIGNER_TOKEN` | engineering-product-designer |
| backend-engineer | `ENGINEERING_AGENT_BOT_BACKEND_ENGINEER_TOKEN` | engineering-backend-engineer |
| frontend-engineer | `ENGINEERING_AGENT_BOT_FRONTEND_ENGINEER_TOKEN` | engineering-frontend-engineer |
| qa-engineer | `ENGINEERING_AGENT_BOT_QA_ENGINEER_TOKEN` | engineering-qa-engineer |
| devops-engineer | `ENGINEERING_AGENT_BOT_DEVOPS_ENGINEER_TOKEN` | engineering-devops-engineer |

per-token 절차 — 한 봇씩 다음을 그대로 따른다.

1. **새 토큰 생성**: Discord Developer Portal → 해당 application → **Bot** → **Reset Token**. 새 값은 곧바로 OS secret manager(macOS Keychain / 1Password / Bitwarden) 에 붙여넣고, 임시 클립보드 내용은 즉시 비운다.
2. **`.env.local` 갱신**: 해당 env key 한 줄만 새 값으로 교체. 다른 토큰은 건드리지 않는다. 파일을 git diff 로 보지 말 것 — `.env.local` 은 gitignore 됐지만 실수로 볼 수 있다.
3. **runtime 재시작 (§11.3)**: 해당 봇만 재시작하면 충분하지만 같은 시점 노출이라면 §11.3 의 일괄 재시작 사용.
4. **검증**: 새 토큰으로 로그인 성공이 `journalctl` 또는 `yule runtime status` 에서 보이는지만 확인. **로그에 토큰 그대로 출력하는 디버그 켜지 않는다.**
5. **이전 토큰 무효화 확인**: Developer Portal 에서 reset 직후 이전 토큰은 자동 invalid 가 되지만, 외부 캐시(GitHub Actions secret / CI / 외부 협업자) 에 남아 있을 수 있으므로 24h 내 점검.

#### 11.2.2 Planning bot (1 종)

| 봇 | env key | Discord app |
|---|---|---|
| Planning bot | `DISCORD_BOT_TOKEN` | yule-planning |

절차는 §11.2.1 과 동일. 단, planning-bot 은 engineering 큐에 의존하지 않으므로 재시작 영향이 가장 작다.

#### 11.2.3 비-Discord secret (참고)

다음은 본 P0 의 직접 대상은 아니지만 같은 노출 경로를 탔다면 함께 rotate.

- `NAVER_APP_PASSWORD` (Naver CalDAV 앱 비밀번호) — Naver 마이페이지에서 재발급.
- `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY` — 각 provider 콘솔에서 재발급. 사용량 알림이 떠 있으면 도용 의심.
- 그 외 `REFERENCE_*` slot 은 현재 wired 되어 있지 않으므로 노출 영향 적음.

### 11.3 Runtime restart 절차 (rotation 직후)

토큰을 갈아끼운 직후 **이전 토큰을 캐싱 중인 프로세스 메모리** 가 남는다. 다음 절차로 강제 cycle.

```bash
# (A) systemd production
sudo systemctl restart yule-run-service@eng-discord-gateway.service
sudo systemctl restart yule-run-service@eng-role-tech-lead.service
sudo systemctl restart yule-run-service@eng-role-backend-engineer.service
sudo systemctl restart yule-run-service@eng-role-frontend-engineer.service
sudo systemctl restart yule-run-service@eng-role-qa-engineer.service
sudo systemctl restart yule-run-service@eng-role-devops-engineer.service
sudo systemctl restart yule-run-service@eng-role-ai-engineer.service
sudo systemctl restart yule-run-service@eng-role-product-designer.service
# planning-bot 이 별도 unit 으로 떠 있다면 그 service 도 재시작.

# (B) 단일 호스트(yule runtime up)
yule runtime down       # 또는 Ctrl-C 로 종료
yule runtime up         # 새 토큰으로 다시 spawn
yule runtime status     # 모든 봇이 ALIVE / queue worker 가 비어 있지 않은지 확인

# (C) 단일 봇만 빠르게
yule run-service eng-discord-gateway        # 또는 다른 service-id
```

queue 워커(`eng-research-worker` 등) 는 토큰을 들고 있지 않으므로 (A)/(B) 의 일괄 재시작에 포함되긴 하지만 토큰 cycle 의 직접 대상은 아니다.

### 11.4 Git history / screenshot / log 위생

토큰이 한 번 git 에 들어가면 reset 만으로는 끝나지 않는다.

- **commit message / diff** 에 토큰이 포함됐다면 push 전이라 해도 **재현 가능성** 이 있다고 보고 즉시 rotate(§11.2). pull/clone 한 작업본이 어딘가에 남아 있다.
- **이미 push 됐다면**: rotate 가 1 순위(이전 토큰을 무효로 만드는 것이 history 정리보다 더 중요). 이후 GitHub support → secret scanning bypass / history rewrite 검토. 강제 history rewrite(force-push) 는 사용자 승인 후에만 진행.
- **screenshot / 영상** 에 노출됐다면 SNS / 채팅 / 메일 / 캡처 도구 캐시(macOS 미리보기, Notion 업로드, Slack 스레드) 까지 검색해 모두 삭제.
- **journalctl / log** 에 출력됐다면 호스트의 `journalctl --vacuum-time=…` 또는 로그 rotate. 외부 로그 수집기(예: Datadog) 가 있다면 거기서도 삭제 요청.
- **외부 LLM / pair-programming 도구** 컨텍스트에 들어갔다면 해당 서비스의 conversation 삭제 + retention policy 확인. 일부 서비스는 학습 캐시에 남으므로 토큰 자체를 무효로 만드는 것이 유일한 안전책.

> 이 운영 문서 자체에는 절대 실제 토큰 / hex prefix / 부분 문자열을 적지 않는다. 노출 사례가 발생했다면 별도 incident note 에 기록하되 그 노트도 `.env.local` 과 같은 등급으로 다룬다(공개 vault / 공개 PR 금지).

### 11.5 M13 Live-test readiness gating

라이브 회귀 / production restart 은 **secret rotation 이 완전히 끝난 뒤에만** 시작한다. 미완료 상태에서 라이브 검증을 돌리면 새 노출 경로가 생긴다.

- `policies/runtime/agents/engineering-agent/live-regression.md` §0.4 가 본 §11 의 완료를 prerequisite 로 명시.
- 미완료 상태 신호:
  - Developer Portal 에서 마지막 token reset 시각이 노출 시점보다 이른 봇이 하나라도 있음.
  - `.env.local` 에 이전 토큰이 그대로 남아 있음(파일 modify time 미변경).
  - §11.3 runtime restart 가 실행되지 않았음(=현 프로세스가 이전 토큰을 메모리에 캐싱).
- M13 readiness 체크리스트(아직 별도 문서 없음)에 추가될 라인:
  - [ ] §11 P0 Secret Hygiene 완료 — 노출된 토큰 9 개 모두 rotate, runtime restart 끝, ≥24h 외부 캐시 점검.

### 11.6 사용자 직접 액션 — 자동화 불가

본 봇은 절대 secret 을 직접 다루지 않는다. 아래는 **운영자/사용자** 가 손으로 해야 한다.

1. Discord Developer Portal 에서 9 개 봇 모두 **Reset Token** 클릭.
2. 새 토큰을 OS secret manager 에 보관 후 `.env.local` 의 해당 env key 한 줄씩 교체.
3. §11.3 의 runtime restart 명령 실행.
4. §11.4 의 화면/로그/외부 컨텍스트 위생 점검.
5. 노출된 시점, rotate 한 시점, 영향 범위를 incident note 에 기록(.env.local 과 동일 등급으로 보관).
6. 본 §11 체크리스트가 모두 끝났을 때 비로소 라이브 회귀 / production runtime 을 다시 띄운다.

