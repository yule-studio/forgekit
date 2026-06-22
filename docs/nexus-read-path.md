# Nexus read path

Hephaistos reads Nexus as an **external** knowledge source — it never copies Nexus into the
repo. Code: `forgekit_console/hephaistos/nexus_read.py`. Flow: `source ref → resolve → read →
bounded normalize → attach`.

## Status semantics (honest, no fake-read)
| status | meaning |
| --- | --- |
| `not_connected` | `FORGEKIT_NEXUS_ROOT` (env) / `nexus_root` (config) unset — the default |
| `missing` | connected but the path is absent |
| `blocked` | present but unreadable (permission / TCC / sandbox) |
| `restricted` | present but raw read gated → **projection_only** for non-allowed roles |
| `exists` | read + bounded normalize |

- **bounded normalize** — title / summary (≤500 chars) / key points / one snippet (≤300 chars) /
  troubleshooting / decision. Never a full raw dump.
- **restricted projection** — a non-allowed role gets title/why only, never the raw body;
  allowed roles (design-lead, privacy-officer, …) get the bounded read.
- An unreadable status **never fabricates content**.

Evidence: [`examples/hephaistos/nexus-read-foundation/`](../apps/forgekit-console/examples/hephaistos/nexus-read-foundation/)
(connected / not_connected / restricted projection). Surfaced via the `nexus` line in `/resolve`.

## Vault bootstrap — honest Obsidian validation + opt-in scaffold (`nexus_vault.py`)

`connection_status` 는 "root 가 set·readable 인가"만 답한다. **vault bootstrap**(`hephaistos/nexus_vault.py`)
는 운영자가 알아야 할 더 풍부한 질문에 정직하게 답한다 — 연결된 root 가 실제 **Obsidian vault**(진짜
`.obsidian/`)인가, 비었는가, note 가 몇 개인가, ForgeKit KB layout(`00-inbox/10-projects/20-areas/
30-resources`)이 있는가 — 그리고 **opt-in 으로 누락 KB dir 를 scaffold** 한다.

honesty rails:
- `is_obsidian` 은 실제 `.obsidian/` 검사 — 없으면 `markdown root`(Obsidian 으로 위조 안 함).
- `inspect_vault` 상태: `not_connected/missing/blocked/empty/connected`(빈 readable root 는 `empty`이되
  connected). note count 는 bounded(대형 vault 는 `N+`).
- `scaffold_vault(create=False)` 는 gap **보고만**, `create=True` 만 KB dir 생성하고 created/existing 을
  정직 보고. **`.obsidian` 은 절대 만들지 않음**(가짜 vault 금지). missing root 면 정직 실패.
- `connection_status` 는 connected 일 때만 `is_vault`/`note_count`/`empty` 키를 **추가**(기존 키 불변, back-compat).

`/nexus set <path>`(persist) 위에 `nexus_ops.apply_bootstrap(path, create=)` 가 연결+검사+scaffold 를
한 번에 — 영속(canonical config) 후 재실행에도 유지. 코드 SSoT `packages/hephaistos/src/hephaistos/
nexus_vault.py`, 회귀 `tests/forgekit/test_nexus_vault.py`, evidence
[`examples/nexus-vault-bootstrap/`](../apps/forgekit-console/examples/nexus-vault-bootstrap/).

## Live surface wiring (WT2)
The read path was real but the surfaces called it with no env/config, so `/nexus`·`/resolve`·
`/hephaistos` showed a static `not_connected`. WT2 threads the live env + config through
`ConsoleContext.env` / `.config` / `.nexus_role` → `commands/router.py` → `hephaistos/projection.py`,
so the surfaces now reflect the REAL root:

- **`/nexus`** — live `connection_status` (root / status / reason). 5-way honest: `not_connected`
  (no root) · `exists` (real readable root) · `missing` (path absent) · `blocked` (unreadable) ·
  `restricted` (raw-gated → projection_only).
- **`/nexus set <path>`** — operator connects: persists `nexus_root` into `config.json`
  (`hephaistos/nexus_ops.py`) and reports the **honest resulting status** (a not-yet-cloned path
  shows `missing`, not `connected`). **`/nexus clear`** disconnects.
- **`/resolve <req>` / `/skills <req>`** — the `nexus` line is live: `read N / missing N / blocked N /
  restricted N` against the actual root. Not connected → honest `not_connected`, never a fake read.
- **role** — `FORGEKIT_NEXUS_ROLE` (or `ConsoleContext.nexus_role`) gates restricted raw vs
  projection_only. Default (empty / `operator`) → **projection_only** for restricted sources.

### Where it is live vs blocked (honest)
- **live** — any local Nexus repo path set via `/nexus set` / env / config; real `.md` reads, bounded.
- **not_connected** — default (no root). The honest zero-state; surfaces say so, no fabrication.
- **missing / blocked** — root set but path absent / unreadable (permission/TCC). Surfaced as-is.
- **restricted** — present but raw-gated; non-allowed roles get title/why projection only.

Evidence: [`examples/nexus-live-read/`](../apps/forgekit-console/examples/nexus-live-read/),
tests `test_nexus_live_read` + `test_nexus_read`.
