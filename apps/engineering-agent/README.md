# engineering-agent

> 개발 작업을 받아 코드 작업으로 끌고 가는 엔지니어링 앱. 구 모놀리스
> `src/yule_orchestrator` 가 `src/yule_engineering/` 으로 **이동 + 전역 rename**
> 되어 본 앱이 보유한다(루트 `src/` 및 `yule_orchestrator` 모듈은 제거됨).
>
> 과도기: `yule_engineering` 안에는 아직 분해되지 않은 agents 코어와,
> packages/* 및 다른 app(planning/discord) 을 가리키는 compat shim 이 공존한다.
> agents 코어의 thoughtful 분해는 `docs/monorepo-structure.md §4` 로드맵 참조.

## 책임 범위

- 개발 작업 **intake** — 자연어 요청을 코드 작업 단위로 분류/접수.
- **코드 작업 계획** — 작업 분해, 범위 산정, 작업 순서.
- **role deliberation** — 역할별(council) 검토/합의 흐름.
- **GitHub issue·PR 작업 연동** — 이슈/브랜치/PR 생성 및 갱신.

> 범위 밖: Discord transport(=discord-gateway), 일정/브리핑(=planning-agent),
> memory housekeeping(=memory-worker).

## 의존 패키지 (필요한 `packages/*` 만)

- `packages/agent-contracts` — 다른 agent / gateway 와 주고받는 command /
  event / status 스키마. **agent 간 직접 import 금지**, 본 contracts 만 사용.
- `packages/runtime` — job queue / lifecycle (예정).
- `packages/llm-gateway` — LLM 요청 (예정).
- `packages/memory` — 검색이 필요할 때만 read-only 로 의존 (예정).

> 규칙: `apps/* → packages/*` 만 허용, 역방향 금지. agent 끼리는
> contracts(command/event/status)로만 연결.

## 현재 위치 → 이전 대상

| 현재 위치 | 이전 대상 |
| --- | --- |
| `apps/engineering-agent/src/yule_engineering/agents/**` | `apps/engineering-agent/**` (코어) + `packages/runtime`, `packages/agent-contracts` 로 분리 |
| `apps/engineering-agent/src/yule_engineering/discord/engineering_channel_router/**` | command/event 변환부는 `apps/discord-gateway`, 의사결정 로직은 본 앱 |

## migration TODO

- [ ] `agents/**` 중 deliberation / planning / GitHub 연동 코어를 본 앱으로 이전.
- [ ] agent 간 직접 호출을 `packages/agent-contracts` command/event/status 로 교체.
- [ ] `engineering_channel_router` 의 transport 부분을 `apps/discord-gateway` 로 분리, 본 앱은 command/event 만 처리.
- [ ] runtime(job queue/lifecycle)을 `packages/runtime` 으로 추출 후 본 앱에서 의존.

(실제 코드 이동은 본 브랜치 범위 아님 — 후속 PR.)
