# ForgeKit vision

ForgeKit goes from "a personal automation toolkit" toward a **bounded, senior-team-like
runtime** — many providers/agents/tools wired into one operator-gated forge that carries
real work to a finish, with everything recorded for reuse.

## Why the split (ForgeKit / Nexus / Hephaistos / Armory)
- **Nexus** = the mine/library: where knowledge (areas/patterns/snippets/troubleshooting/
  decisions) lives. Source of truth for *what we know*.
- **ForgeKit** = the forge/platform: the execution environment, operator surfaces, runtime,
  approval, memory.
- **Hephaistos** = the smith: reads Nexus knowledge and forges it into Skills / Loadout /
  Weapons / Work Packet an agent equips to finish a job. It is a **core inside ForgeKit**,
  not the whole platform and not a single slash command.
- **Armory** = the smith's catalog: the skill/loadout/weapon manifests.

## Non-goals (now)
- Fully-autonomous, unsupervised code mutation — only **safe-class**, operator-gated.
- Live social/Figma scraping — **planned seams**, never faked live.
- "Complete autonomous team" — agents are bounded and approval-gated by design.

Related: [hephaistos-runtime.md](hephaistos-runtime.md) · [README](../README.md).
