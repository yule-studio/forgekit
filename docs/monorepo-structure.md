# Monorepo 구조 (목표 + 진행)

> 본 doc 은 monorepo 의 **목표 구조** 와 **현재 진행 상황** 을 한 곳에 둔다.
> 운영자 진입점이며, 실제 코드 배치는 `apps/` README 들과
> `src/yule_orchestrator/**` (현재 위치) 가 SSoT 다.

## 1. 목표 구조

```
apps/        실행 단위(앱) — 좁은 책임, 진입점
  engineering-agent/   개발 작업 intake / 계획 / deliberation / GitHub 연동
  planning-agent/      일정 / 계획 / 브리핑
  discord-gateway/     Discord transport (command/event 로만 연결)
  memory-worker/       reindex / retrieval eval / vault sync / housekeeping
  loadtest-runner/     runtime/memory/agent backend 부하 (MOCK 대상)

packages/    공용 라이브러리 — 앱이 의존, 앱을 import 하지 않음
  agent-contracts/     command / event / status 스키마
  memory/              검색 / 인덱싱 코어
  llm-gateway/         LLM 요청 게이트웨이
  runtime/             job queue / lifecycle
```

## 2. 의존 방향 규칙 (hard rail)

- `apps/* → packages/*` 가능, **역방향 금지**.
- **agent 간 직접 import 금지** — `packages/agent-contracts` 의 command /
  event / status 로만 연결.
- **discord-gateway 는 command/event 로만 연결** — agent 내부 로직 직접 수행 금지.

## 3. 현재 진행 상황

| 영역 | 상태 |
| --- | --- |
| `packages/agent-contracts` | done (스키마 확정) |
| `packages/memory` | in progress |
| `packages/llm-gateway` | in progress |
| `packages/runtime` | in progress |
| `apps/*` (책임 문서/scaffold) | scaffolded — 본 브랜치 `refactor/apps-layout` |
| 실제 코드 이전 | **미착수** (후속 PR) — 코드는 여전히 `src/yule_orchestrator/**` |

> 본 브랜치는 **scaffold/문서 전용** — 코드 이동 / compat shim / package 추출
> 없음. 각 앱의 migration TODO 는 해당 `apps/<app>/README.md` 참조.
