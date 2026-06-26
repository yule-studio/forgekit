# Evidence map

Where each capability's evidence lives. Paths under `apps/forgekit-console/examples/` are tracked;
`runs/` artifacts are **gitignored** (regenerate via the noted command).

| capability | evidence path | tracked? | regenerate |
| --- | --- | --- | --- |
| Hephaistos resolve (MVP) | `examples/hephaistos/` (+ `test_hephaistos`) | yes | `pytest`-style unittest |
| Nexus read foundation | `examples/hephaistos/nexus-read-foundation/` | yes | see its README |
| per-provider usage breakdown | `examples/usage/per-provider-breakdown/` | yes | `/usage` (breakdown lines) |
| usage ledger / live vs estimate | `examples/usage/` (+ `native-usage-live/`) | yes | `/usage` writes `runs/forgekit/usage/` |
| runtime-teeth (submit gate) | `examples/runtime-teeth/` | yes | — |
| always-on daemon | `examples/runtime/` (`daemon-execution/`, heartbeat) | yes | `forgekit runtime serve --max-ticks N` |
| autopilot safe-class execution | `examples/autopilot/` (`source-mutation/`) | yes | `/autopilot <repo>` |
| handoff (PM→gateway→tech-lead) | `examples/handoff/`, `examples/e2e/bkurs/` | yes | `/pm-agent` |
| notifications | `examples/notify/` | yes | — |
| discovery / sources | `examples/sources/`, `examples/discovery/` | yes | `/sources` |
| design restricted source | `examples/design/` | yes | `/design` |
| security red/blue | `examples/security/` | yes | `/red-blue` |
| vault authorship | `examples/vault/` | yes | — |
| goal evidence → Nexus 축 (schema 고정) | `examples/goal/nexus-evidence/` | yes | `/goal publish <id>` (연결 vault) |

Runtime output written by the daemon/autopilot lands under `<repo>/runs/forgekit/…`
(**gitignored** — `apps/forgekit-console/runs/` is ignored; committed evidence lives in `examples/`).
