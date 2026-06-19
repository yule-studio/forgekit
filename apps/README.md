# apps/ — 실행 단위(앱) 인덱스

> 본 디렉터리는 ForgeKit 플랫폼(umbrella)의 **실행 단위(앱)** 를 모은다. 각 앱은
> 명확하고 좁은 책임 범위를 가지며, ForgeKit 코어/공용 로직은 `packages/*` 가 소유한다.
> 앱은 `packages/*` 만 바라보고 **서로 직접 import 하지 않는다**(§2).
>
> **`forgekit-console` 은 ForgeKit 을 조작하는 operator app 이다 — ForgeKit 플랫폼 자체가
> 아니다.** 단, 현재는 ForgeKit 코어(runtime/provider/hephaistos/nexus/armory/config/
> contracts)가 아직 `apps/forgekit-console/src/forgekit_console/**` 안에 물리적으로
> 들어있다. 이를 `packages/*` 코어로 분리하는 작업이 진행 중이다 — owner 매트릭스와
> 로드맵 SSoT 는 [`docs/forgekit-architecture-ownership.md`](../docs/forgekit-architecture-ownership.md).

## 1. 앱 목록

| 앱 | 책임 한 줄 | 현재 코드 위치 |
| --- | --- | --- |
| [`forgekit-console`](forgekit-console/README.md) | ForgeKit operator app — TUI/CLI/operator surface (보여주고 조작) | `apps/forgekit-console/src/forgekit_console/**` (코어는 packages/forgekit-*·hephaistos·nexus 로 분리됨; console 은 surface + 옛 경로 compat shim) |
| [`engineering-agent`](engineering-agent/README.md) | 개발 작업 intake / 코드 작업 계획 / role deliberation / GitHub 연동 | `apps/engineering-agent/src/yule_engineering/agents/**`, `discord/engineering_channel_router/**` |
| [`planning-agent`](planning-agent/README.md) | 일정·계획·브리핑, calendar 기반 작업 정리 | `apps/engineering-agent/src/yule_engineering/planning/**` |
| [`discord-gateway`](discord-gateway/README.md) | Discord 메시지 수신/전송, agent runtime I/O 채널 연결 | `apps/engineering-agent/src/yule_engineering/discord/**` |
| [`memory-worker`](memory-worker/README.md) | memory reindex / retrieval eval / vault sync / housekeeping | `apps/engineering-agent/src/yule_engineering/memory/**`, `cli/memory.py` |
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

ForgeKit 코어(현재 `forgekit-console` 내부)를 `packages/*` 로 분리한다:

- `packages/forgekit-runtime` — runtime loop / daemon / approval·notify / lifecycle / autopilot / runtime_mode
- `packages/forgekit-provider` — provider/model routing / usage gate / policy / submit service / brain
- `packages/forgekit-config` — config schema / env·persistence / runtime paths / agent identity
- `packages/forgekit-contracts` — command / event / status / work-packet 스키마
- `packages/hephaistos` — skill/loadout/work-packet **forging core** (slash command 아님)
- `packages/nexus` — knowledge source read/projection/retrieval **boundary**
- `packages/armory` — skill/loadout/weapon manifest **catalog**

shared infra(여러 app 공용): `packages/core` · `storage` · `integrations` · `security` ·
`vcs` · `llm-gateway` · `memory`. transitional(engineering-agent 산하): `agent-contracts` ·
`agent-memory` · `agent-runtime` · `learning` · `runtime`(=primitives).

> ForgeKit 코어인지 / 공용 infra 인지 / 과도기인지의 **분류표·네이밍 충돌·migration matrix·
> "어디에 새 기능 추가" 결정 트리** 는 [`docs/package-topology.md`](../docs/package-topology.md) 가 SSoT.

자세한 목표 구조·진행 상황은 [`docs/monorepo-structure.md`](../docs/monorepo-structure.md),
owner 매트릭스·로드맵은 [`docs/forgekit-architecture-ownership.md`](../docs/forgekit-architecture-ownership.md) 참조.
