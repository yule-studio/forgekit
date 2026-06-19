<p align="center">
  <img src="assets/forgekit.png" alt="ForgeKit" width="100%">
</p>

# ForgeKit

> **ForgeKit is a personal agent-operations forge** — you wire up AI providers, equip
> agents with the right skills for a job, run them under operator-gated runtime policy,
> and keep every result in a durable memory. It is **not another chatbot**: it orchestrates
> *multiple* providers, agents and tools to carry a real task to a bounded finish.

*forge* (a smithy — heat metal, hammer it to shape) + *kit* (a set of tools).

## What ForgeKit is

ForgeKit is the **platform (umbrella)**. Its engine lives in `packages/*`; the things under
`apps/*` are **execution apps** that share that engine — none of them *is* ForgeKit:
- **`forgekit-console`** — the **operator app**: a Claude-Code-style TUI to configure the
  brain, switch runtime modes, resolve work, and read honest status. It shows and operates;
  it should not own platform core.
- **`engineering-agent`** (`yule`) — an always-on, role-based engineering runtime (Discord
  gateway + member bots, SQLite job queue, Obsidian vault mirror).
- plus `planning-agent` · `discord-gateway` · `memory-worker` · `loadtest-runner`.

The ForgeKit core — runtime / provider / config / contracts / **Hephaistos** / **Nexus** /
**Armory** — lives in `packages/*` (extracted out of `forgekit_console/`; the console keeps
only the operator surface). Alongside the ForgeKit cores, `packages/*` also holds **shared
infra** (core / storage / integrations / vcs / security / llm-gateway / memory) used by the
`engineering-agent` family, plus some **transitional** agent packages. The full classification
(which package is platform core vs named core vs shared infra vs transitional, the naming
collisions, and where to add a new feature) is in
[`docs/package-topology.md`](docs/package-topology.md); owner matrix + extraction roadmap in
[`docs/forgekit-architecture-ownership.md`](docs/forgekit-architecture-ownership.md).

Everything is provider-neutral (Claude / Codex / Gemini / Ollama and any openai-compatible
or enterprise endpoint sit behind one contract) and operator-gated (approval / budget /
safe-class boundaries are real, not decorative).

## Core concepts

| Concept | One line | Code |
| --- | --- | --- |
| **ForgeKit** | the whole platform / execution environment | this repo |
| **Hephaistos** | the *skill-forging core* — turns a request into an equip plan (agent + skills + loadout + weapons + work packet) | `forgekit_console/hephaistos/` |
| **Nexus** | the external knowledge source (areas / patterns / snippets / troubleshooting) Hephaistos *reads* (never copies) | read path: `hephaistos/nexus_read.py` |
| **Armory** | the catalog of Skills / Loadouts / Weapons Hephaistos forges from | `packages/armory` (`armory.catalog` / `armory.models`) |
| **Work Packet** | a structured, executable unit (goal / scope / forbidden / commands / verify / approval / evidence) | `hephaistos/models.py` |
| **Runtime Mode** | the operator posture (Shift+Tab) that actually changes routing / budget / approval | `policy/runtime_mode.py` |

Mine → library → smith: **Nexus is the mine/library, ForgeKit is the forge, Hephaistos is the smith.**

> The `Code` column above is the **current** location (still inside `forgekit_console/`). These are
> ForgeKit **core**, not console-private — the WT1–WT4 refactor moves their owner to
> `packages/{hephaistos,nexus,armory,forgekit-runtime,forgekit-provider,...}`. See the
> [ownership matrix](docs/forgekit-architecture-ownership.md). Hephaistos/Nexus/Armory are cores,
> not slash commands; the console only renders their projection.

## Current reality (honest)

Status is one of **working** / **partial** / **planned** / **blocked** — see the matrices
in [`docs/operator-surfaces.md`](docs/operator-surfaces.md) and [`docs/evidence-map.md`](docs/evidence-map.md).

| Capability | Status | Surface | Evidence |
| --- | --- | --- | --- |
| Multi-provider config + routing (no implicit ollama) | **working** | submit path, `/mode` | `examples/runtime-teeth/` |
| Vendor-native usage ledger (live vs estimate) | **working** | `/usage` | `examples/usage/` |
| Hephaistos resolve (request → equip plan) | **working** | `/resolve` `/hephaistos` `/skills` `/loadout` | `examples/hephaistos/` |
| Armory breadth (7 categories, 25 skills, 8 loadouts) | **working** | `/resolve` | `test_armory_breadth` |
| Nexus read path (bounded, restricted-aware) | **partial** | `/resolve` nexus line | `examples/hephaistos/nexus-read-foundation/` |
| Always-on bounded daemon + safe-class autopilot | **working** | `forgekit runtime serve` | `examples/runtime/`, `examples/autopilot/` |
| Provider `/provider` console config (set primary / list / doctor) | **working** | `/provider` | `test_provider_surface` |
| Nexus live repo connection | **planned** (`not_connected` until `FORGEKIT_NEXUS_ROOT` set) | — | — |
| Live Figma / YouTube / Google / Instagram | **planned seam** (never live in this tree) | `/sources` shows planned | — |

**No fake-live.** Anything not wired is shown as `planned` / `not_connected` / `blocked` /
`restricted` / `unsupported_in_console`, never as if it works.

## Quick start

```bash
pip install -e '.[console]'   # console extra (textual + image render)
forgekit                      # open the operator console TUI
yule --help                   # the engineering runtime CLI (runtime/harness/doctor/…)
```

First run with **no provider configured** → the console reports `setup-required` and free-text
submit is held. **You choose the brain** — ForgeKit does **not** silently use a reachable
local Ollama (implicit local fallback is OFF by default).

## Provider / setup / doctor / mode

- **`forgekit` opens inline by default** (Claude-Code-style: lives in the existing terminal
  flow, native scrollback/selection, terminal-native background). `forgekit --full` (or
  `FORGEKIT_UI_MODE=full`) is the escape hatch to the alternate-screen TUI.
- **Primary brain ≠ actual live transport.** You set the primary brain, but a free-text submit
  follows `slot_routing.default_chat` to the **actual live provider**. claude/codex are
  **routing/brain participants** (no console live-submit yet — `unsupported_in_console`);
  **gemini/ollama are the current live lane**. So `primary=claude` with `default_chat=gemini`
  is a normal, healthy state — the submit goes to gemini (live), surfaced honestly as
  `declared claude → actual gemini`, never "Submitting to claude" then dying.
- **`/provider preset four-brain`** wires the recommended 4-provider brain in one command:
  `primary=claude`, `linked=[claude,codex,gemini,ollama]`, `default_chat/research→gemini`,
  `execution→codex`, `compression/classification→ollama`, `safety/synthesis→claude`, with
  explicit per-slot fallback orders. It writes a real, persisted config (survives restart).
- **You set the primary provider.** `~/.forgekit/config.json` carries `primary_provider` +
  `linked_providers` + `slot_routing` + `fallback_policy` (legacy `main_provider` is migrated).
  See [`docs/forgekit-provider-policy.md`](docs/forgekit-provider-policy.md).
- **No implicit Ollama.** With no config, submit is `setup-required` — a reachable Ollama is
  only used if you explicitly set `fallback_policy.implicit_local_fallback: true`. The
  four-brain preset connects providers **explicitly** (no implicit/silent ollama).
- **`/doctor`** checks environment readiness (render backend, runtime, provider posture).
- **`/mode`** (Shift+Tab) cycles runtime modes — each changes real routing / budget / approval
  (not just a label). `approval-wait` truly holds submits; `cost-save` biases cheap routing.
- **usage `live` vs `estimate`** — `/usage` records native provider usage as `live` when the
  response carries a usage block (ollama openai-compat does), else an honest length `estimate`.
  The two are never summed.

## Operator console overview

`/resolve <req>` (equip plan) · `/hephaistos` (forge status) · `/skills <req>` · `/loadout <id>`
(real env verify) · `/usage` · `/mode` · `/doctor` · `/render` · `/whoami` (agent identity) ·
`/autopilot <repo>` · `/digest` · `/sources` (discovery) · `/blocked`. Full honest matrix:
[`docs/operator-surfaces.md`](docs/operator-surfaces.md).

Example — `/resolve "Spring Boot JWT refresh token"` →
`backend-engineer` + skills `java-spring, auth-jwt, mysql` + loadout `backend-java-local` +
weapons + nexus refs (`not_connected` until configured) + a Work Packet draft.

## Always-on runtime (honest limits)

`forgekit runtime serve` is a **real** bounded daemon (observe → safe-class execute → verify →
record), but **macOS lid-close suspends it** — **Linux / homeserver / systemd is the 1st-class
always-on path**. See [`apps/forgekit-console/examples/runtime/`](apps/forgekit-console/examples/runtime/).

The `engineering-agent` runtime (`yule runtime up`, Discord member bots, SQLite queue, Obsidian
mirror) is the production multi-bot path — see [`docs/operations.md`](docs/operations.md).

## Architecture at a glance

```
Inputs/Connectors → Hephaistos (forge: resolve → packet) → Agents (PM/gateway/tech-lead/specialists)
                                        ↑ reads                         ↓ bounded execution
                                      Nexus (knowledge)            Memory / Vault (SQLite + Obsidian)
```

## Docs map

| Topic | Doc |
| --- | --- |
| Vision / why the split | [docs/vision.md](docs/vision.md) |
| Package topology (apps vs packages, 분류표, 어디에 추가) | [docs/package-topology.md](docs/package-topology.md) |
| Hephaistos runtime | [docs/hephaistos-runtime.md](docs/hephaistos-runtime.md) |
| Nexus read path | [docs/nexus-read-path.md](docs/nexus-read-path.md) |
| Armory (skills/loadouts) | [docs/armory.md](docs/armory.md) |
| Work Packet | [docs/work-packet.md](docs/work-packet.md) |
| Operator surfaces (reality matrix) | [docs/operator-surfaces.md](docs/operator-surfaces.md) |
| Evidence map | [docs/evidence-map.md](docs/evidence-map.md) |
| Console guide / policy | [docs/forgekit-console.md](docs/forgekit-console.md) |
| Provider policy | [docs/forgekit-provider-policy.md](docs/forgekit-provider-policy.md) · [docs/provider-capability-matrix.md](docs/provider-capability-matrix.md) |
| Operations (always-on) | [docs/operations.md](docs/operations.md) |
| Config / memory / testing | [docs/configuration.md](docs/configuration.md) · [docs/memory.md](docs/memory.md) · [docs/testing.md](docs/testing.md) |

Reading order for contributors/agents: [`AGENTS.md`](AGENTS.md) → [`CLAUDE.md`](CLAUDE.md) → topical `docs/<topic>.md`.

## Roadmap / non-goals

**Next:** Nexus live connection · per-provider usage
surface · GitHub-App doctor/commit-path. **Non-goals (now):** fully-autonomous unsupervised code
mutation (safe-class only, operator-gated), live social/Figma scraping, "complete autonomous team".

## Contributing / license

Personal project, pre-external-contribution. Commit format:
[policies/reference/COMMIT_CONVENTION.md](policies/reference/COMMIT_CONVENTION.md). Retrospectives /
decisions go in the Obsidian vault, not the README.
