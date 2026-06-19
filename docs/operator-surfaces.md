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

## Provider reality matrix
| provider | connection | live submit | usage basis | mode influence |
| --- | --- | --- | --- | --- |
| ollama (local, openai-compat) | zero-config if running | yes | **live** (native usage) | yes |
| openai / gemini (openai-compat) | api key | yes (when keyed) | live (when reported) else estimate | yes |
| claude / codex (CLI) | routable | **no** (`unsupported_in_console`) | n/a | routing only |

The operator **sets `primary_provider`**; no implicit local fallback (a reachable ollama is NOT used
unless `fallback_policy.implicit_local_fallback: true`). No provider → `setup-required`.

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
| Nexus vault | not_connected (until `FORGEKIT_NEXUS_ROOT`) | restricted projection for some roles | `examples/hephaistos/nexus-read-foundation/` |
| restricted design source | blocked (TCC) | design role only (else projection) | `examples/design/` |
| Figma | planned seam | — | — |
| YouTube / Google / Instagram | planned | — | — |
| GitHub / HN / Reddit / RSS | working (injected fetcher; free-first) | rate/ToS | `examples/sources/` |
