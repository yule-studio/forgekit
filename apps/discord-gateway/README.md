# discord-gateway

> Discord 메시지를 받고 보내는 **transport 게이트웨이**. agent runtime 의
> input/output 채널을 연결한다. 코드는 `apps/discord-gateway/src/yule_discord/**`
> 에 있으며, 옛 경로 `apps/engineering-agent/src/yule_engineering/discord/**` 에는 동일 객체로
> 해소되는 **compat shim** 만 남아 있다.

## 책임 범위

- **Discord 메시지 수신/전송** — 채널/포럼/멤버 메시지 I/O.
- **agent runtime input/output 채널 연결** — 들어온 메시지를 command/event 로
  변환해 agent 로 넘기고, agent 가 낸 event/status 를 Discord 로 내보낸다.

> **agent 내부 로직 직접 수행 금지.** gateway 는 deliberation / 코드 작업 /
> 계획 같은 의사결정을 절대 수행하지 않는다. 의사결정은
> engineering-agent / planning-agent 의 몫.

## 패키지 위치

| 구분 | 경로 |
| --- | --- |
| 정규 코드 | `apps/discord-gateway/src/yule_discord/**` (`yule_discord` import) |
| 옛 경로 shim | `apps/engineering-agent/src/yule_engineering/discord/**` (54 모듈, `sys.modules` alias) |

옛 경로 shim 은 importlib `sys.modules[__name__] = yule_discord.<P>` 형태로,
`yule_engineering.discord.X.Y is yule_discord.X.Y` 객체 동일성을 보존한다.
신규 코드는 `yule_discord` 를 직접 import 한다.

## 의존 관계

- **직접 third-party** — `discord.py>=2.4.0` (Intents / ext.commands /
  LoginFailure / Object 사용).
- **clean deps (`packages/*`)** — `core` / `storage` / `integrations` /
  `memory` 의 shared infra 를 `yule_engineering.<top>` 절대 import 로 사용
  (현재는 monorepo path 로 해결, pip dependency 미선언).

### 과도기 edge (acyclic, 후속 PR 에서 contract 로 대체 예정)

- `yule_discord → yule_engineering.agents` / `yule_engineering.runtime`
  — **apps → monolith**. gateway 가 agent runtime 의 deliberation / job_queue /
  routing / lifecycle 함수를 직접 호출하는 과도기 edge. acyclic
  (agents 는 discord 를 import 하지 않음 — 순환 이미 제거됨).
- `yule_discord → yule_engineering.planning`
  — **app → app**. planning 도메인은 이미 `apps/planning-agent` 로 이전됐고,
  `yule_engineering.planning` 은 그 shim 이다. 즉 discord-gateway →
  planning-agent (app → app) 과도기 edge 로, 후속에 `packages/agent-contracts`
  command/event/status 계약으로 대체된다.

> 목표 방향: gateway↔agent 연결을 `packages/agent-contracts` command/event/
> status 로 일원화하고 직접 함수 호출을 제거한다. 그 전까지 위 edge 는 모두
> acyclic 과도기로 유지된다.

## migration TODO

- [ ] `engineering_channel_router` 등 router 안의 agent 의사결정 로직을 분리해
  해당 agent 앱으로 보냄 (현재는 `yule_engineering.agents.*` 직접 호출).
- [ ] gateway↔agent 연결을 `packages/agent-contracts` command/event/status 로
  일원화 (직접 함수 호출 제거).
- [ ] 옛 경로 shim (`apps/engineering-agent/src/yule_engineering/discord/**`) 제거 — 모든 importer 가
  `yule_discord` 직접 import 로 전환된 후.
