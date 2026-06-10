# apps/ — 실행 단위(앱) 인덱스

> 본 디렉터리는 monorepo 의 **실행 단위(앱)** 를 모은다. 각 앱은 명확하고
> 좁은 책임 범위를 가지며, 공통 로직은 향후 `packages/*` 로 추출한다.
> 본 브랜치(`refactor/apps-layout`)는 **scaffold/문서 전용** 이며 실제 코드
> 이전은 후속 작업이다. 현재 코드는 여전히
> `src/yule_orchestrator/**` 에 있다.

## 1. 앱 목록

| 앱 | 책임 한 줄 | 현재 코드 위치 |
| --- | --- | --- |
| [`engineering-agent`](engineering-agent/README.md) | 개발 작업 intake / 코드 작업 계획 / role deliberation / GitHub 연동 | `src/yule_orchestrator/agents/**`, `discord/engineering_channel_router/**` |
| [`planning-agent`](planning-agent/README.md) | 일정·계획·브리핑, calendar 기반 작업 정리 | `src/yule_orchestrator/planning/**` |
| [`discord-gateway`](discord-gateway/README.md) | Discord 메시지 수신/전송, agent runtime I/O 채널 연결 | `src/yule_orchestrator/discord/**` |
| [`memory-worker`](memory-worker/README.md) | memory reindex / retrieval eval / vault sync / housekeeping | `src/yule_orchestrator/memory/**`, `cli/memory.py` |
| [`loadtest-runner`](loadtest-runner/README.md) | runtime/memory/agent backend 부하 테스트 (MOCK 대상) | (신규, 코드 이전 없음) |

## 2. 의존 방향 규칙 (hard rail)

- **`apps/* → packages/*` 가능, 역방향 금지.** packages 는 앱을 import 하지
  않는다. 공용 로직은 항상 packages 쪽으로 내린다.
- **agent 간 직접 import 금지.** engineering-agent 가 planning-agent 의
  내부 모듈을 직접 부르지 않는다. agent 사이는 `agent-contracts` 의
  **command / event / status** 메시지로만 연결한다.
- **discord-gateway 는 command/event 로만 연결.** gateway 는 메시지를
  command/event 로 변환해 전달하고, agent 출력 event 를 받아 Discord 로
  내보낼 뿐 agent 내부 로직을 직접 수행하지 않는다.

```
discord-gateway ──(command/event)──▶ engineering-agent / planning-agent
        ▲                                   │
        └───────────(event/status)──────────┘

apps/*  ──▶  packages/*   (역방향 ✗)
agent ──X──▶ agent  (직접 import 금지, contracts 경유)
```

## 3. 향후 구조

- `packages/agent-contracts` — command / event / status 스키마 (예정/일부 완료)
- `packages/memory` — 검색/인덱싱 코어 (예정)
- `packages/llm-gateway` — LLM 요청 게이트웨이 (예정)
- `packages/runtime` — job queue / lifecycle (예정)

자세한 목표 구조와 진행 상황은 [`docs/monorepo-structure.md`](../docs/monorepo-structure.md) 참조.
