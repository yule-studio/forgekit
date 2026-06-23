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

## Coverage (8 categories, 28 skills, 10 loadouts)
| category | skills |
| --- | --- |
| backend | java-spring, kotlin-spring, node-nestjs, python-fastapi |
| frontend | react-typescript, nextjs, vite-react |
| database | mysql, postgres, redis |
| devops | docker, kubernetes, terraform, aws-ecs, github-actions |
| security | auth-jwt, oauth2, secrets-management, web-security-review |
| ai | openai-api, rag-basics, eval-harness, agent-evaluation |
| docs | docs-quality (built-in prose, tool-less) |
| design-support | figma-read (partial — blocked when unconnected), design-system-review, ux-ui-reference-pack |

Loadouts: backend-java/python/node-local, frontend-react-local, fullstack-web-local,
devops-cloud-local, ai-agent-local, security-review-local, docs-writing-local (tool-less),
design-review-local.

## Manifest contract (no placeholders)
Each skill carries id/title/category/summary/when_to_use/when_not_to_use/required_inputs/
expected_outputs/commands/verify/**unsafe_boundary**/signals/capability_note/nexus_refs, plus the
**attach contract** (`kind` / `provider_affinity` / `install_requirements` / `attach_requirements`).
Each loadout carries goal/recommended/optional/**blocked**_skills/selection_signals — so the resolver
can *explain why* a combo was chosen. Validity is locked by `test_armory_breadth` (referential
integrity + non-placeholder guard).

### Entry kind + attach contract
`kind ∈ {skill, tool, plugin, mcp}` (`armory.models.ENTRY_KINDS`):
- **skill** — knowledge / workflow / convention the executor already carries (no external attach).
- **tool** — a CLI/binary the executor invokes → needs `install_requirements`.
- **plugin** — a harness plugin/extension → needs `attach_requirements` + `provider_affinity`.
- **mcp** — an MCP server → needs install/attach + `provider_affinity` (which harness it binds to).

`provider_affinity` names the **attachment target** (claude-code / codex / github / mcp-host …) — an
*attachment fact*, not a capability claim, so it MAY name a vendor. `capability_note` stays
vendor-neutral (the breadth test guards only that field). `ATTACH_REQUIRED_KINDS = (tool, plugin, mcp)`
cannot be equipped without an explicit install/attach step — promoting one without it would be a fake
"available" entry, so the promotion gate rejects it.

Uncovered stacks (e.g. Rust embedded) resolve **shallow** — honest, never faked. Adding a stack =
add a manifest + signals; the resolver picks it up.

## Intake → promotion (`armory.candidate`)
New entries do not have to be hand-edited into the seed. An **`ArmoryCandidate`** (a proposal from
discovery / a curated note / an operator, carrying its `source`/`source_ref` provenance) is gated by
**`promote_candidate(candidate) → PromotionResult`**:
- **Reject** (with reasons) if the contract is incomplete — placeholder/short summary, no signals, no
  `when_to_use`, no `unsafe_boundary`, vendor-locked `capability_note`, no verify path, or a
  tool/plugin/mcp missing its install/attach (and mcp/plugin missing `provider_affinity`).
- **Accept** → a real `SkillSpec` plus an **evidence trail** of which gates passed. No partial
  promotion: an incomplete candidate is rejected wholesale, so the catalog never gains a stub.

A promoted spec is registered into a **runtime overlay** (`catalog.register_promoted` /
`promoted_skills` / `clear_overlay`) that `all_skills()`/`skill()` merge over the seed — so the
resolver picks it up immediately, re-promotion (same id) updates in place, and tests `clear_overlay()`
for isolation. Evidence: `apps/forgekit-console/examples/armory-intake/intake.txt`.

### Adoption review — 도입 효율 검토 (adopt-now / collect-first / hold)
`promote_candidate` is the *schema* gate; **`AdoptionReview`** is the *should-we-adopt-at-all*
gate. It carries 8 fields (current pain / expected benefit / overlap with existing / operational
cost / maintenance risk / provider-runtime fit / governance-security impact / adopt-timing reason)
and **≥3 axis reviews** (PM + tech-lead + ≥1 specialist). `disposition()` = the most-conservative
axis, gated on completeness → any axis voting hold/collect-first pulls it down; a missing field or
axis ⇒ **hold** (no single-axis adopt). **`adopt_candidate(candidate, review)`** couples both: only
`adopt-now` + a valid contract yields a `SkillSpec` to register; `collect-first`/`hold` return no
spec (no fake adoption — evidence kept for Nexus). **adopted** (in catalog/overlay) stays distinct
from **equipped** (the tools are actually installed/attached for a task — checked by the execution
core, see [hephaistos-runtime.md](hephaistos-runtime.md)).

## Context-aware selection (Hephaistos)
`hephaistos.resolve(request, *, preferred_role="", project_facts=(), runtime_constraints=(), harness="")`
folds project/runtime context into the existing selection surface (no new routing layer):
- **project_facts** — Nexus/operator context. An *exclusion* fact ("EKS는 제외", "k8s 빼고") drops the
  matching catalog skill(s) and records it as a `forbidden_scope` line; a non-exclusion fact
  ("dev 환경부터", "기존 구조 보존") becomes a packet **constraint**.
- **runtime_constraints** — provider/runtime limits (e.g. "production apply 금지") → packet constraints.
- **harness** — intended executor (claude-code / codex …) recorded on the packet.

Every pick *and* exclusion emits a **`SelectionEvidence`** row (target / kind / decision / reason /
signals) — a "smart" choice with no evidence is a fake and does not ship; a shallow request renders an
honest `(근거 없음)` rather than a fabricated rationale. The console projects this trail read-only
(`projection.selection_evidence_lines`). Representative scenario (Terraform→ECS, EKS excluded,
dev-first, keep-structure) is locked by `test_armory_intake_promotion`.
