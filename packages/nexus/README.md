# nexus

> **Nexus** — ForgeKit 의 knowledge source **boundary**. 지식이 사는 광산/도서관이며,
> Hephaistos 가 *읽어* 작업으로 forge 하는 read/projection/retrieval 경계다. ForgeKit /
> Nexus / Hephaistos / Armory 중 한 축의 독립 코어이지, console 사유 모듈도 slash command
> 하나도 아니다 ([`docs/vision.md`](../../docs/vision.md)).

WT3 승격의 일부. Owner 매트릭스:
[`docs/forgekit-architecture-ownership.md`](../../docs/forgekit-architecture-ownership.md).

## 보유 모듈
- `nexus.sources` — discovery source collector (GitHub/HN/Reddit/RSS, free-first). pure leaf.
- `nexus.vault` — Obsidian vault read + authorship(`forgekit_config.identity` registry 기반).

## 후속 (entangled, 다음 increment)
- `discovery` — handoff/contracts 분리 후.
- `design` — restricted source. core 는 nexus, UI projection 은 console 으로 분리(WT4).
- `nexus_read` — bounded read path. 현재 hephaistos 안. hephaistos 코어 정리와 함께.

## 의존 규칙
- `forgekit-config`(identity)만 의존. `apps/*` import 금지(역방향 hard rail).
- 옛 `forgekit_console.{sources,vault}` 는 본 package 의 동명 모듈 shim(`_compat.alias_package`).
