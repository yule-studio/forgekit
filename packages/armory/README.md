# armory

> **Armory** — ForgeKit 의 **catalog / capability registry**. "무엇이 있는가": Skills /
> Loadouts / Weapons 의 inventory 와 그 spec vocabulary. Hephaistos(대장장이)가 plan 을
> forge 할 때 읽는 카탈로그다. ForgeKit / Nexus / Hephaistos / Armory 중 한 축의 독립 코어.

RWT2 승격. Owner 매트릭스:
[`docs/package-topology.md`](../../docs/package-topology.md) · [`docs/armory.md`](../../docs/armory.md).

## 보유 모듈
- `armory.models` — spec vocabulary: `SkillSpec` / `LoadoutSpec` / `WeaponSpec` / `RuneSpec`
  + `NexusSourceRef` + NEXUS_*/SRC_*/WEAPON_* 상수. (구 `hephaistos.models` 에서 분리)
- `armory.catalog` — 카탈로그 데이터 + accessor: `all_skills/all_loadouts/all_weapons/
  skill/loadout/weapon/categories`. (구 `hephaistos.armory`)

## Hephaistos 와의 경계
- **Hephaistos** = resolve / orchestration / work-packet / loadout **selection** 코어
  (forge-output 타입 `WorkPacketDraft` / `ResolvedForgePlan` 는 hephaistos 소유).
- **Armory** = "무엇이 존재하는가" **catalog** 코어. armory 는 hephaistos 를 import 하지
  않는다 → hephaistos → armory 단방향(순환 없음).

## 옛 경로 (compat)
- `hephaistos.armory` → `armory.catalog` re-export shim.
- `hephaistos.models` → `armory.models` 의 catalog 타입을 re-export(`from hephaistos.models
  import SkillSpec` 계속 동작).
