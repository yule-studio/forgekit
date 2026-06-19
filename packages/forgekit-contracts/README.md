# forgekit-contracts

> ForgeKit **contracts core** — the pure, stdlib-only schema (command-result kinds,
> interaction/layout modes, structured result/packet dataclasses) shared between
> ForgeKit core and its surfaces. No textual/IO, so the whole core is testable without
> a terminal.

Part of the **WT2** extraction. Owner matrix:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 보유 모듈
- `forgekit_contracts.models` — command-result kind 상수 + console mode/layout 상수 +
  결과/패킷 dataclass. 구 `forgekit_console.models` 는 본 모듈 shim(`sys.modules` 별칭).

## 기존 agent-contracts 와의 구분
`packages/agent-contracts`(`yule_agent_contracts`)는 **agent↔agent** command/event/status.
본 package 는 **operator/console-facing** command-result/work-packet 스키마로 레이어가
다르다. 중복 정의 금지 — 겹치면 한쪽이 다른 쪽을 re-export 한다.

## 의존 규칙
- stdlib only. `apps/*` import 금지(역방향 hard rail). 다른 forgekit-* 코어가 본 package 를
  의존한다(역은 금지) — contracts 는 가장 낮은 leaf.
