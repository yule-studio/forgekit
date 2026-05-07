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
│   │   └── agent.json
│   └── planning-agent/
│       ├── CLAUDE.md
│       └── agent.json
├── policies/
│   ├── reference/                  # commit / branch / naming convention
│   └── runtime/                    # lifecycle / role weight / live regression
├── docs/                           # 사용자용 문서 허브
├── deploy/                         # systemd unit (always-on)
├── scripts/
│   └── bootstrap
└── src/
    └── yule_orchestrator/
        ├── cli/                    # yule CLI (doctor, planning, engineer, discord, …)
        ├── core/                   # 공용 유틸 (timezone, dispatcher 등)
        ├── diagnostics/
        ├── discord/                # gateway + member bot 런타임
        ├── integrations/           # GitHub / CalDAV / TLS
        ├── planning/               # planning agent 정책 엔진
        └── agents/
            ├── lifecycle/          # Phase 1-7 status / persistence / role_selection
            ├── research/           # collector / loop / pack / sufficiency
            ├── obsidian/           # export / writer / approval / git
            ├── coding/             # authorization / job
            ├── reports/            # work_report / meeting_minutes
            ├── messaging/          # message / dispatcher / registry
            ├── runners/            # 모델 어댑터 (Claude / Codex / Gemini / Ollama)
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

## 데이터 저장소

- `.cache/yule/cache.sqlite3` — 단일 SQLite 파일. 테이블: `local_cache_entries` (workflow session, planning snapshot, calendar cache, task_completion_events, …).
- `.cache/yule/memory.sqlite3` — 메모리 FTS5 인덱스.
- `Obsidian vault` — 결정 / 리서치 / 회의록 결정적 export. layout 정책: `policies/runtime/agents/engineering-agent/obsidian-memory.md`.

## 프로세스 모델

- **dev** — `yule discord up` 이 multiprocessing 으로 9 프로세스 spawn (planning + gateway + 7 멤버). 부모는 즉시 종료.
- **prod (권장)** — systemd template unit 으로 각 봇이 독립 service. 자세한 가이드 + 큐 / heartbeat / 상태 머신 설계: [operations.md](operations.md).

## 정책 위치

- `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` — engineering 운영 정책 본문
- `policies/runtime/agents/engineering-agent/live-regression.md` — 사람이 직접 돌리는 라이브 회귀 4 시나리오
- `policies/runtime/agents/engineering-agent/role-weights-v0.md` — 역할 가중치
- `policies/reference/COMMIT_CONVENTION.md` — 커밋 메시지 source of truth
- `policies/reference/BRANCH_STRATEGY.md` — 브랜치 정책
- `policies/reference/NAMING_CONVENTION.md` — 네이밍 정책
