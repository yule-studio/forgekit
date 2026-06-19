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

## Where the boundary lives (umbrella vs apps vs core)
ForgeKit is the **platform (umbrella)**. `apps/forgekit-console` is the **operator app** —
the cockpit that *shows and operates* ForgeKit; it is **not** ForgeKit itself.
`engineering-agent / planning-agent / discord-gateway / memory-worker / loadtest-runner` are
sibling **execution apps** under the same umbrella — they share ForgeKit core via `packages/*`,
they do **not** import each other or live inside the console.

The engine (runtime / provider / config / contracts / Nexus / Hephaistos / Armory) lives in
`packages/*`, beside the **shared-infra** packages (core / storage / integrations / …) used by
the engineering-agent family. The full package classification — platform core vs named core vs
shared infra vs transitional, plus where to add new work — is in
[package-topology.md](package-topology.md); owner matrix + import boundary in
[forgekit-architecture-ownership.md](forgekit-architecture-ownership.md).

## Non-goals (now)
- Fully-autonomous, unsupervised code mutation — only **safe-class**, operator-gated.
- Live social/Figma scraping — **planned seams**, never faked live.
- "Complete autonomous team" — agents are bounded and approval-gated by design.

Related: [forgekit-architecture-ownership.md](forgekit-architecture-ownership.md) ·
[hephaistos-runtime.md](hephaistos-runtime.md) · [README](../README.md).
