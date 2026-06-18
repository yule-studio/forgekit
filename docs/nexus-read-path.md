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
