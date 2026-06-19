# hephaistos

> **Hephaistos** — ForgeKit 의 skill-forging **core**(대장장이). 요청을 읽어 Armory 에서
> equip plan(agent + skills + loadout + weapons + Nexus refs + Work Packet)을 forge 하고
> 로컬 loadout 을 검증한다. ForgeKit / Nexus / Hephaistos / Armory 중 한 축의 독립 코어이지,
> console 모듈도 slash command 하나도 아니다 ([`docs/vision.md`](../../docs/vision.md)).

WT3 승격. Owner 매트릭스:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 보유 모듈
- `hephaistos.models` — Skill/Loadout/Weapon/WorkPacket/ResolvedForgePlan 타입 (pure leaf).
- `hephaistos.armory` — skill/loadout/weapon **catalog** (현재 hephaistos 내부; 후속에
  `packages/armory` 승격 검토 — models 분리 선행 필요).
- `hephaistos.resolver` / `projection` / `verifier` — forge core + 검증 + projection.
- `hephaistos.nexus_read` / `nexus_ops` — Nexus(`packages/nexus`)에 대한 bounded **reader**
  (smith 가 mine 을 읽는 도구). store/source 자체는 `nexus` package 소유.

## 의존 규칙
- `forgekit-config`(paths)만 의존. `apps/*` import 금지(역방향 hard rail).
- 옛 `forgekit_console.hephaistos` 는 본 package shim(`_compat.alias_package`).
