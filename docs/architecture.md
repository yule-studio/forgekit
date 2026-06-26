# Architecture

## 디렉토리 구조

```text
.
├── AGENTS.md
├── CLAUDE.md
├── GEMINI.md
├── README.md
├── agents/
│   ├── engineering-agent/
│   │   ├── CLAUDE.md
│   │   └── manifest.json
│   └── planning-agent/
│       ├── CLAUDE.md
│       └── manifest.json
├── policies/
│   ├── reference/                  # commit / branch / naming convention
│   └── runtime/                    # lifecycle / role weight / live regression
├── docs/                           # 사용자용 문서 허브
├── deploy/                         # systemd unit (always-on)
├── scripts/
│   └── bootstrap
└── src/
    └── yule_engineering/
        ├── cli/                    # yule CLI (doctor, planning, engineer, discord, runtime, …)
        ├── core/                   # 공용 유틸 (timezone, dispatcher 등)
        ├── diagnostics/
        ├── discord/                # gateway + member bot 런타임
        ├── integrations/           # GitHub / CalDAV / TLS
        ├── planning/               # planning agent 정책 엔진
        ├── runtime/                # always-on runtime (M6+) — services / supervisor
        │                           # status / circuit_breaker / fallback / status_poster
        └── agents/
            ├── lifecycle/          # Phase 1-7 status / persistence / role_selection
            ├── research/           # collector / loop / pack / sufficiency
            ├── obsidian/           # export / writer / approval / git
            ├── coding/             # authorization / job
            ├── reports/            # work_report / meeting_minutes
            ├── messaging/          # message / dispatcher / registry
            ├── runners/            # 모델 어댑터 (Claude / Codex / Gemini / Ollama)
            ├── job_queue/          # SQLite-backed worker queue (M1+) — store / workers /
            │                       # heartbeat / standalone_runners / approval_discord_poster
            └── runtime/            # role-runtime, deliberation hooks
```

## 주요 모듈 책임

| 모듈 | 책임 |
|---|---|
| `discord/bot.py` | planning gateway entrypoint (`yule discord bot`) |
| `discord/engineering_channel_router.py` | engineering 라우팅 / intake / 작업 thread / coding gate |
| `discord/engineering_team_runtime.py` | open-call / role-turn / synthesis runtime |
| `discord/member_bot.py` | 역할 멤버 봇 entrypoint |
| `discord/typing_indicator.py` | typing context + heartbeat (Phase 1) |
| `agents/lifecycle/role_selection.py` | active_research_roles 산출 |
| `agents/lifecycle/status.py` | research_status / report_status / Obsidian gate |
| `agents/lifecycle/persistence.py` | session.extra 머지 / persist_thread_link 등 |
| `agents/research/collector.py` | 자율 수집기 + role × provider |
| `agents/research/loop.py` | research_loop 엔드포인트 |
| `agents/research/pack.py` | ResearchPack / Source / Finding 데이터 모델 |
| `agents/coding/authorization.py` | tech-lead 의 coding 권한 제안 (Phase 2 research-only 분기 포함) |
| `agents/reports/work_report.py` | WorkReport 빌드 + Phase 6 게이트 |
| `agents/obsidian/*` | Obsidian export / writer / approval / git |
| `agents/job_queue/*` | SQLite job queue, role/research/approval/obsidian writer workers, heartbeat (M1–M5) |
| `runtime/services.py` | engineering profile inventory (`ServiceSpec` × 12) |
| `runtime/run_service.py` | `yule run-service <id>` entrypoint (systemd / runtime up parent) |
| `runtime/subprocess_supervisor.py` | `yule runtime up` parent — spawn / backoff / circuit-aware restart |
| `runtime/circuit_breaker.py` | per-service breaker + SQLite persistence + `yule runtime circuit reset` |
| `runtime/fallback.py` | role-take scan → degrade banner / deterministic fallback synthesis + audit |
| `runtime/status.py` + `runtime/status_summary.py` | `yule runtime status` data model + markdown formatter |
| `runtime/status_poster.py` | `#봇-상태` Discord poster (dedup-key gated) |
| `runtime/gateway_env.py` | engineering gateway env carve-out (planning-bot 채널 차단) |

## 데이터 저장소

- `.cache/yule/cache.sqlite3` — 단일 SQLite 파일. 테이블: `local_cache_entries` (workflow session, planning snapshot, calendar cache, task_completion_events, …).
- `.cache/yule/memory.sqlite3` — 메모리 FTS5 인덱스.
- `Obsidian vault` — 결정 / 리서치 / 회의록 결정적 export. layout 정책: `policies/runtime/agents/engineering-agent/obsidian-memory.md`.

## 프로세스 모델

운영 / dev 두 경로 모두 standalone runtime 이 1급 진입점이다.

- **always-on 단일 호스트** — `yule runtime up --profile engineering` 한 명령으로 12 개 service (supervisor + research + 7 role + approval + obsidian writer + discord gateway) 를 spawn. backoff + circuit-break 로 낱개 service 장애를 흡수한다. M6.0+ 도입.
- **production** — systemd template unit 이 service 별로 `yule run-service <service-id>` 호출. 같은 코드를 부모만 systemd 로 바꿔 띄우는 구조. M6+. 자세한 가이드 + 큐 / heartbeat / 상태 머신 / fallback / status posting: [operations.md](operations.md).
- **dev / 단독 호스트 multiprocessing** — `yule discord up` 이 multiprocessing 으로 9 프로세스 spawn (planning + gateway + 7 멤버). 빠른 로컬 검증 / 단일 명령 부트스트랩용 dev launcher. 제거 / deprecation 되지 않으나 운영 경로는 아니다.

## 정책 위치

- `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` — engineering 운영 정책 본문
- `policies/runtime/agents/engineering-agent/live-regression.md` — 사람이 직접 돌리는 라이브 회귀 4 시나리오
- `policies/runtime/agents/engineering-agent/role-weights-v0.md` — 역할 가중치
- `policies/reference/COMMIT_CONVENTION.md` — 커밋 메시지 source of truth
- `policies/reference/BRANCH_STRATEGY.md` — 브랜치 정책
- `policies/reference/NAMING_CONVENTION.md` — 네이밍 정책

## Local-first Agent Control Plane (1차 폴더링)

> ForgeKit 의 확장 방향. 디렉터리 책임은 [foldering.md](foldering.md), 오케스트레이터는
> [hephaistos.md](hephaistos.md). 본 절은 1차 scaffolding 의 역할 지도다.

**정의** — ForgeKit 은 로컬 우선(Local-first) 으로 도는 **에이전트 제어판**이다. 판단/계획은
로컬에서 하고, 외부 연결과 무거운 작업만 위임한다.

**Local / Server 역할 분리**
- **Local** — forge-cli/daemon/dashboard, Hephaistos 판단, openclaw 로컬 실행, nexus/armory 저장.
  기본 신뢰 경계. 사람 승인 게이트가 여기 있다.
- **Server(선택)** — 무거운/공유가 필요한 작업만(원격 실행·대용량 인덱싱 등). 기본은 로컬.

**구성요소 역할**
| 요소 | 역할 |
| --- | --- |
| **ForgeKit Core** (`packages/forge-core`) | 플랫폼 골격·제어판 진입 |
| **Hephaistos** (`agents/hephaistos`) | 최상위 오케스트레이터·판단 엔진(도구 선택/계획/승격) |
| **Hermes** (`agents/hermes`) | 외부 세계 연결자(검색/브라우저/API/메시지/웹 조사) |
| **OpenClaw** (`agents/openclaw`) | 로컬 실행자(터미널/파일/Git/브라우저 조작) |
| **Nexus** (`nexus/`) | 기억 저장소(작업기록/평가/판단기준/지식) |
| **Armory** (`armory/`) | 도구 창고(프롬프트/스킬/스크립트/어댑터/레시피) |
| **Labs** (`labs/`) | 실험장(clone coding / CLI agent 조사 / 패턴 추출) |

**실행 흐름**
```text
User → forge-cli / forge-dashboard
     → ForgeKit Core
     → Hephaistos (intent → plan → tool-select)
     → Policy (autonomy level / approval gate)
     → Hermes / OpenClaw / Runtimes (실행)
     → Nexus / Armory (기록·승격)
```
