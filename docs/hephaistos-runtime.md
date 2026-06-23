# Hephaistos runtime

Hephaistos is ForgeKit's **skill-forging core** — pure (`forgekit_console/hephaistos/`), the
console is a projection layer over it. Flow:

```
request → resolve (infer domain/lang/framework/topic) → score Armory skills (+ language gate)
        → pick loadout (signals + recommended overlap) → required weapons
        → attach Nexus refs (read path; honest status) → Work Packet draft
```

- **resolver** (`resolver.py`) — rule-first, deterministic, explainable. Language gate excludes
  a Java skill for a Python request (FastAPI → `python-fastapi`, not `java-spring`).
- **verifier** (`verifier.py`) — loadout readiness against the real env (`ready/partial/missing/blocked`).
- **models** (`models.py`) — Skill/Loadout/Weapon/Rune/WorkPacketDraft/NexusSourceRef/ResolvedForgePlan.

## What works / what doesn't (honest)
- **working**: resolve for the covered stacks (backend/frontend/db/devops/security/ai/design-support),
  loadout verify, work packet draft, operator surfaces (`/resolve`·`/hephaistos`·`/skills`·`/loadout`).
- **partial**: Nexus read (foundation only — `not_connected` until `FORGEKIT_NEXUS_ROOT` set).
- **planned**: per-skill install/equip automation (verify-only today), figma-read live.
- Uncovered requests resolve **shallow** (honest, never faked).

Runtime posture (mode/approval/budget) is the existing `policy/runtime_mode.py` EffectivePolicy —
a mode change changes real routing/budget/approval. See [operator-surfaces.md](operator-surfaces.md).

## Execution core (`execution.py`) — `/forge`

`resolve` is the *selection* engine; **`forge_execution_plan`** turns its plan into an
execution-ready packet via four steps (each injectable → deterministic tests):

```
resolve → equip (adopted vs equipped) → Nexus enrich → ponytail (anti-overbuild) → assemble
```

- **equip** — *adopted* = the selected skills; *equipped* = the loadout/skill tools actually
  present locally (probed with an injectable `which`). A tool can be **adopted but NOT
  equipped** — that gap is surfaced (`not_equipped` + install steps), never hidden. A tool-less
  skill-only task (docs prose) is `ready` with nothing to install.
- **Nexus enrich** — reads the plan's Nexus refs (`nexus_read`, honest: `not_connected`/`missing`
  stay so) and folds the real project rules/points into the packet scope → project-specific.
- **ponytail** (`ponytail.py`) — the **anti-overbuild lens**, a reviewer not an approver. Emits
  one of three verdicts so the surface knows whether to escalate:
  `review-required` (prod-touch unguarded / tool-sprawl) · `consult-required` (a NEW capability,
  or an unconstrained broad loadout — "만능 loadout 금지") · `waived-with-reason` (proportionate;
  built-ins suffice). Every verdict carries its findings — no silent pass. **Ponytail never
  replaces the tech-lead**; `review-required` means *escalate to* one.
- **assemble** — goal / selected skills+tools / rejected candidates (+why: language-gate /
  project-fact / loadout-scope / domain-gate) / constraints / verification plan / expected
  outputs / runtime+approval implications.

`/forge <요청>` projects this read-only (`projection.execution_lines`). Representative scenarios
(Terraform→ECS · FE design system · docs prose) are locked by `test_hephaistos_execution_core`;
evidence: `apps/forgekit-console/examples/hephaistos-execution/execution.txt`.

## Adoption discipline (`armory.candidate`) — adopted ≠ equipped

External skill/tool/plugin/mcp candidates are not added on "looks good". The **도입 효율 검토**
artifact (`AdoptionReview`) carries 8 fields (current pain / expected benefit / overlap /
operational cost / maintenance risk / provider-runtime fit / governance-security impact /
adopt-timing reason) and **≥3 axis reviews** (PM + tech-lead + ≥1 specialist). `disposition()`
is the most-conservative axis, gated on completeness → **adopt-now / collect-first / hold**; a
missing field or axis ⇒ `hold` (no single-axis adopt, no fake adoption). `adopt_candidate`
couples this with the schema gate (`promote_candidate`): only `adopt-now` + a valid contract
returns a `SkillSpec` to `register_promoted`. `collect-first` keeps evidence for Nexus without
activating. **adopted** (catalog/overlay) and **equipped** (install/attach satisfied per task)
stay distinct — see [armory.md](armory.md).
