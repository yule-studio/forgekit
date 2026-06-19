# Armory

The catalog Hephaistos forges from — now its own package **`packages/armory`** (RWT2):
- `armory.catalog` — the catalog data + accessors (`all_skills` / `all_loadouts` / `all_weapons` /
  `skill` / `loadout` / `weapon` / `categories`). Was `hephaistos/armory.py`.
- `armory.models` — the spec vocabulary (`SkillSpec` / `LoadoutSpec` / `WeaponSpec` / `RuneSpec`
  + `NexusSourceRef` + NEXUS_*/SRC_*/WEAPON_*). Split out of `hephaistos.models`.

**Boundary:** Hephaistos = resolve / orchestration / loadout *selection* / work-packet
(forge-output types `WorkPacketDraft` / `ResolvedForgePlan` stay in `hephaistos.models`).
Armory = "무엇이 존재하는가" catalog. **Armory imports nothing from Hephaistos** → `hephaistos →
armory` single direction (no cycle). Old paths kept as compat: `hephaistos.armory` re-exports
`armory.catalog`; `hephaistos.models` re-exports the catalog vocab from `armory.models`
(so `from hephaistos.models import SkillSpec` still works). **Provider-neutral**: skills describe
*capability / framework / workflow* (`capability_note` carries the capability lens), never a vendor name.

## Coverage (7 categories, 25 skills, 8 loadouts)
| category | skills |
| --- | --- |
| backend | java-spring, kotlin-spring, node-nestjs, python-fastapi |
| frontend | react-typescript, nextjs, vite-react |
| database | mysql, postgres, redis |
| devops | docker, kubernetes, terraform, aws-ecs |
| security | auth-jwt, oauth2, secrets-management, web-security-review |
| ai | openai-api, rag-basics, eval-harness, agent-evaluation |
| design-support | figma-read (partial — blocked when unconnected), design-system-review, ux-ui-reference-pack |

Loadouts: backend-java/python/node-local, frontend-react-local, fullstack-web-local,
devops-cloud-local, ai-agent-local, security-review-local, design-review-local.

## Manifest contract (no placeholders)
Each skill carries id/title/category/summary/when_to_use/commands/verify/**unsafe_boundary**/
signals/capability_note/nexus_refs. Each loadout carries goal/recommended/optional/**blocked**_skills/
selection_signals — so the resolver can *explain why* a combo was chosen. Validity is locked by
`test_armory_breadth` (referential integrity + non-placeholder guard).

Uncovered stacks (e.g. Rust embedded) resolve **shallow** — honest, never faked. Adding a stack =
add a manifest + signals; the resolver picks it up.
