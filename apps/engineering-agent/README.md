# engineering-agent

> 개발 작업을 받아 코드 작업으로 끌고 가는 엔지니어링 앱. 본 디렉터리는
> 현재 **책임 문서(scaffold)** 만 두며, 코드는 아직
> `src/yule_orchestrator/**` 에 있다.

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
| `src/yule_orchestrator/agents/**` | `apps/engineering-agent/**` (코어) + `packages/runtime`, `packages/agent-contracts` 로 분리 |
| `src/yule_orchestrator/discord/engineering_channel_router/**` | command/event 변환부는 `apps/discord-gateway`, 의사결정 로직은 본 앱 |

## migration TODO

- [ ] `agents/**` 중 deliberation / planning / GitHub 연동 코어를 본 앱으로 이전.
- [ ] agent 간 직접 호출을 `packages/agent-contracts` command/event/status 로 교체.
- [ ] `engineering_channel_router` 의 transport 부분을 `apps/discord-gateway` 로 분리, 본 앱은 command/event 만 처리.
- [ ] runtime(job queue/lifecycle)을 `packages/runtime` 으로 추출 후 본 앱에서 의존.

(실제 코드 이동은 본 브랜치 범위 아님 — 후속 PR.)
