# Operator surfaces — reality matrix

Honest status of each console surface (working / partial / planned). Code:
`commands/registry.py` + `commands/router.py` + `tui/app.py`.

## Capability reality matrix
| capability | status | surface | evidence |
| --- | --- | --- | --- |
| Hephaistos resolve | working | `/resolve` `/skills` | `examples/hephaistos/`, `test_armory_breadth` |
| forge status | working | `/hephaistos` | `test_hephaistos_surface` |
| loadout verify (real env) | working | `/loadout <id>` | `test_hephaistos` |
| Nexus read | working (live read when connected; not_connected/missing/blocked/restricted honest) | `/resolve` nexus line | `examples/nexus-live-read/`, `test_nexus_live_read` |
| Nexus connection status | working | `/nexus` | `test_nexus_read`, `test_nexus_live_read` |
| Nexus connect/disconnect (operator) | working | `/nexus set <path>` · `/nexus clear` | `test_nexus_live_read` |
| usage ledger (live vs estimate) | working | `/usage` | `examples/usage/` |
| per-provider/model/mode usage breakdown | working | `/usage` | `examples/usage/per-provider-breakdown/` |
| runtime modes (real routing/budget/approval) | working | `/mode`, Shift+Tab | `examples/runtime-teeth/` |
| always-on daemon + safe-class autopilot | working (bounded) | `forgekit runtime serve`, `/autopilot` | `examples/runtime/`, `examples/autopilot/` |
| daemon heartbeat in console (state/tick/pid/kill-switch) | working | `/daemon` · `/daemon stop` | `examples/runtime-daemon/`, `test_runtime_daemon_surface` |
| agent identity (git author / app status) | working | `/whoami` | `test_identity_attribution` |
| discovery collectors (free-first) | working; YouTube/IG/Google planned | `/sources` | `examples/sources/` |
| design restricted source | blocked (real TCC) — honest | `/design` | `examples/design/` |
| red/blue planning (owned assets) | working (plan-only) | `/red-blue` | `examples/security/` |
| `/provider` console config (set primary / list / doctor) | working | `/provider [set <id>\|list\|doctor]` | `test_provider_surface` |
| provider link / unlink / slot route | working | `/provider [link\|unlink\|route show\|route set <slot> <id>]` | `test_provider_surface` |
| 4-provider brain preset (real config writer) | working | `/provider preset four-brain` | `test_four_brain_preset_and_routing` |
| `/provider` declared→actual per slot (brain vs live transport) | working | `/provider` (default_chat/execution: declared X → actual Y) | `test_four_brain_preset_and_routing` |

## Provider reality matrix
| provider | connection | live submit | usage basis | mode influence |
| --- | --- | --- | --- | --- |
| ollama (local, openai-compat) | zero-config if running | yes | **live** (native usage) | yes |
| openai / gemini (openai-compat) | api key | yes (when keyed) | live (when reported) else estimate | yes |
| claude / codex (CLI) | routable | **no** (`unsupported_in_console`) | n/a | routing only |

The operator **sets `primary_provider`**; no implicit local fallback (a reachable ollama is NOT used
unless `fallback_policy.implicit_local_fallback: true`). No provider → `setup-required`.

**Primary brain ≠ actual live transport.** A free-text submit does NOT hit `primary_provider`
directly — it follows `slot_routing.default_chat` resolved to the **actual live provider**
(declared → actual, explicit fallback surfaced). claude/codex stay **routing/brain participants**
(`unsupported_in_console`); **gemini/ollama are the live lane**. So `primary=claude` +
`default_chat=gemini` resolves the submit to gemini (live) — shown as `declared claude → actual
gemini`, never "Submitting to claude" then dying. Only when *every* linked provider is
`unsupported_in_console` does the submit honestly fail. With a live linked provider (gemini/ollama)
the brain is `configured-live`, NOT `configured-no-live`.

**Submit teeth (WT1 — `chat/service.py`):** the submit path builds an ordered attempt chain
`routing.submit_chain(cfg, default_chat, prefer=<gate target>)` = declared/routed head + the operator's
**explicit `slot_fallback_orders`** + (opt-in only) ollama. If the head is unusable
(`unsupported_in_console` / auth missing / transport error) it falls to the next provider in that order
and the receipt shows the honest hop (`· fallback claude→ollama`). Per-provider `model_overrides[<id>]`
is applied to the actual call (model precedence: override → global `model` → ollama-installed → id).
Evidence: `examples/provider-runtime-core/`, `test_provider_runtime_core`.

## Source reality matrix
| source | read status | restrictions | evidence |
| --- | --- | --- | --- |
| repo-local docs | working | — | `examples/sources/` |
| Nexus vault | working (live read when connected via `/nexus set` / `FORGEKIT_NEXUS_ROOT` / config; not_connected default) | restricted projection for non-allowed roles | `examples/nexus-live-read/` |
| restricted design source | blocked (TCC) | design role only (else projection) | `examples/design/` |
| Figma | planned seam | — | — |
| YouTube / Google / Instagram | planned | — | — |
| GitHub / HN / Reddit / RSS | working (injected fetcher; free-first) | rate/ToS | `examples/sources/` |

## Provider / Nexus / Always-on execution core (WT1–WT4) — honest status

This round wired the three execution axes from "seam present" to "real teeth", with
integration evidence. Truly working / partial / planned / blocked:

| axis | truly working | partial | planned / blocked |
| --- | --- | --- | --- |
| **Provider runtime** | primary/slot routing → submit; **explicit `slot_fallback_orders` fallback** on unusable head; **`model_overrides` per provider**; no-implicit-ollama; unsupported_in_console honest; usage_basis live/estimate | fallback uses the `default_chat` slot order (mode→slot for non-chat slots not yet split in the gate) | per-provider `budget_policy` not yet enforced (global budget only); claude/codex live submit (CLI) |
| **Nexus read** | live root via `/nexus set` / env / config; 5-way status (not_connected/exists/missing/blocked/restricted); real bounded `.md` reads; restricted → projection_only unless role-allowed; surfaced in `/nexus` `/resolve` `/skills` `/hephaistos` | role is a single context value (no per-command `--role`) | remote/non-filesystem Nexus transports |
| **Always-on daemon** | bounded serve loop (heartbeat/kill-switch/max-ticks/cooldown/signals); CLI `serve\|once\|status\|stop`; **console `/daemon` `/daemon stop`** read the same heartbeat; safe-class autopilot tick; approval/alert → inbox + opt-in desktop; systemd/launchd units | TUI shows status only (no in-console approve/deny UI) | auto-install of units; macOS lid-close suspends (Linux/systemd is the 1급 path — honest) |

**Integration evidence:** `examples/integration/scenarios.txt` runs three scenarios
(Spring Boot JWT · Next.js UI · Terraform ECS/K3s) through provider resolution
(+ fallback) → `/resolve` (Hephaistos + live Nexus line) → usage rollup → `/daemon`,
proving the axes compose. Test: `test_integration_provider_nexus_daemon`.

**Honesty rails kept:** no implicit ollama (no-config → setup-required, zero provider
calls); Nexus never fakes a read (not_connected/missing/blocked surfaced as-is); the
daemon `/daemon` surface shows honest `stopped` when no heartbeat exists.
