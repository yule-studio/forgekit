# Operator surfaces — reality matrix

Honest status of each console surface (working / partial / planned). Code:
`commands/registry.py` + `commands/router.py` + `tui/app.py`.

## Capability reality matrix
| capability | status | surface | evidence |
| --- | --- | --- | --- |
| Hephaistos resolve | working | `/resolve` `/skills` | `examples/hephaistos/`, `test_armory_breadth` |
| forge status | working | `/hephaistos` | `test_hephaistos_surface` |
| loadout verify (real env) | working | `/loadout <id>` | `test_hephaistos` |
| Nexus read | partial (not_connected default) | `/resolve` nexus line | `examples/hephaistos/nexus-read-foundation/` |
| Nexus connection status | working | `/nexus` | `test_nexus_read` |
| usage ledger (live vs estimate) | working | `/usage` | `examples/usage/` |
| per-provider/model/mode usage breakdown | working | `/usage` | `examples/usage/per-provider-breakdown/` |
| runtime modes (real routing/budget/approval) | working | `/mode`, Shift+Tab | `examples/runtime-teeth/` |
| always-on daemon + safe-class autopilot | working (bounded) | `forgekit runtime serve`, `/autopilot` | `examples/runtime/`, `examples/autopilot/` |
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

## Source reality matrix
| source | read status | restrictions | evidence |
| --- | --- | --- | --- |
| repo-local docs | working | — | `examples/sources/` |
| Nexus vault | not_connected (until `FORGEKIT_NEXUS_ROOT`) | restricted projection for some roles | `examples/hephaistos/nexus-read-foundation/` |
| restricted design source | blocked (TCC) | design role only (else projection) | `examples/design/` |
| Figma | planned seam | — | — |
| YouTube / Google / Instagram | planned | — | — |
| GitHub / HN / Reddit / RSS | working (injected fetcher; free-first) | rate/ToS | `examples/sources/` |
