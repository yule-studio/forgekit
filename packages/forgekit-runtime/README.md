# forgekit-runtime

> ForgeKit **runtime core** — "ForgeKit 이 시간에 걸쳐 무엇을 하는가"(operator-gated).
> bounded always-on loop · daemon · approval/notify · lifecycle escalation · autopilot ·
> self-improvement. 어떤 app 도 import 하지 않는다.

WT2 추출. Owner 매트릭스:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 보유 모듈
`runtime`(loop/daemon/heartbeat/runbook/surface/autopilot_tick) · `autopilot` ·
`lifecycle`(failure escalation + operator inbox) · `notify` · `selfimprove` · `security`.

## 의존 (packages only)
`forgekit-config`(paths) · `forgekit-provider`(usage) · `forgekit-contracts`(models) ·
`nexus`(sources). **`apps/*` import 없음**(역방향 hard rail 준수).

## 정직한 app seam 2개 (package→app import 아님)
1. **handoff** — intake→packet 브리지는 operator app 소유. core 는
   `runtime.loop.register_handoff_runner(fn)` 로 주입받는다(app 이 adapter 제공).
   console shim(`forgekit_console.runtime`)이 console 의 `run_handoff` 를 등록한다.
2. **lifecycle → yule_engineering troubleshooting ledger** — best-effort, lazy,
   try/except, 없으면 no-op. import-time dep 아님. 남은 debt → agent-contracts event(WT4).

옛 `forgekit_console.{runtime,autopilot,lifecycle,notify,selfimprove,security}` 는 본
package shim(`_compat.alias_package`).
