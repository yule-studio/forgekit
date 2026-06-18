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
