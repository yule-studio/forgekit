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

## Write path — evidence → vault 누적 (`nexus/vault/evidence.py`)

Nexus 는 read-only 가 아니다 — ForgeKit 의 **evidence 가 knowledge plane 에 누적**돼야 한다
(최종 완성 축4). `forgekit_goal` 의 append-only `EvidenceRecord`(ts/kind/summary/ref)를 **인증
vault note**(frontmatter + 5섹션, `nexus.vault.note` writer 재사용)로 써서 연결된 Nexus root 아래
`10-projects/forgekit/evidence/<goal>/NNN-<kind>.md` 에 누적한다.

honesty rails:
- **no fake nexus connection** — `vault_root` 없으면 `not_connected`(노트 0개, 위조 없음).
  goal/스토어 미해결이면 `no_goal`.
- **append-only / idempotent** — note 경로는 (goal, index) 결정적. 기존 note 는 재실행에도
  덮어쓰지 않고 skip(goal evidence 의 append-only 계약과 일치). 파일은 disk 에 영속(restart 유지).
- `forgekit_goal` 은 **lazy best-effort import** — nexus 의 단일 `forgekit-config` 의존 유지.

API: `accumulate_goal_evidence(goal_id, vault_root=, env=)`(goal store → vault) ·
`accumulate_records(goal_id, title, records, vault_root=)`(pure) · `write_evidence_note(...)`.
live 루프에서 evidence append 시 자동 누적하는 wiring 은 runtime seam(후속). 코드 SSoT
`packages/nexus/src/nexus/vault/evidence.py`, 회귀 `tests/forgekit/test_evidence_vault.py`,
evidence [`examples/evidence-vault/`](../apps/forgekit-console/examples/evidence-vault/).
