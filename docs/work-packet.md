# Work Packet

A Work Packet is the **executable unit** Hephaistos drafts from a resolve — not a text blob.
Model: `WorkPacketDraft` in `forgekit_console/hephaistos/models.py`.

Fields: `goal` · `scope` (the skill rules / why) · `forbidden_scope` (unsafe boundaries) ·
`required_areas` (Nexus refs) · `commands` · `verification` · `acceptance` · `approval_level`
(default `L2_internal_approve`) · `evidence_path` · `nexus_refs`.

## Why packets matter (PM → gateway → tech-lead → specialist)
A raw request is not handed straight to implementation. The packet carries the *missing decisions,
forbidden scope, verification, and approval level* so the internal chain (PM intake → gateway route →
tech-lead signoff → specialist) can act safely. Only **safe-class** work auto-executes; risky/
restricted go to approval-wait + runbook. See [docs/forgekit-console.md](forgekit-console.md) §2m
(repo-autopilot) and `examples/autopilot/` for the execution side.

The Hephaistos packet draft is **proposal-only** today — it shapes the work; it does not auto-run.
