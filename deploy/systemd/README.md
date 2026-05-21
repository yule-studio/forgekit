# systemd units for the engineering runtime

`yule run-service <id>` is the canonical worker entrypoint. Both
`yule runtime up` (single-host parent) and these systemd units invoke
the same command — only the surrounding supervision differs.

> **`yule discord up` is NOT a substitute.** That command spawns the
> Discord bots only and does not run the queue workers, so jobs the
> gateway enqueues never get processed. Production (and any single-host
> long-running run) must use either `yule runtime up` or this
> `yule.target` umbrella. See `docs/operations.md` §0.1 for the
> decision tree.

## Files

- `yule-run-service@.service` — template unit. `%i` is the service id.
- `yule.target` — umbrella target so `systemctl start yule.target`
  brings up every enabled instance.
- `yule-plan-snapshot.service` / `.timer` — periodically pushes
  `DailyPlan` snapshot to the `hompage` repo so the homepage can render
  today's plan without contacting the agent directly. Drives
  `scripts/push_plan_snapshot.sh`. See `docs/calendar-notes.md` §Snapshot
  push to hompage.

## Install

```bash
sudo cp yule-run-service@.service /etc/systemd/system/
sudo cp yule.target /etc/systemd/system/
sudo cp /etc/yule/yule-env.conf  # populate with shared env
sudo systemctl daemon-reload
```

## Enable per service

```bash
sudo systemctl enable yule-run-service@eng-supervisor-watch.service
sudo systemctl enable yule-run-service@eng-research-worker.service
sudo systemctl enable yule-run-service@eng-role-tech-lead.service
sudo systemctl enable yule-run-service@eng-role-backend-engineer.service
sudo systemctl enable yule-run-service@eng-role-qa-engineer.service
sudo systemctl enable yule-run-service@eng-role-devops-engineer.service
sudo systemctl enable yule-run-service@eng-role-ai-engineer.service
sudo systemctl enable yule-run-service@eng-approval-worker.service
sudo systemctl enable yule-run-service@eng-obsidian-writer.service
# Spring-only stage: leave frontend / product-designer disabled.
```

Each enabled instance reads `/etc/yule/yule-env.conf` (shared) and
optionally `/etc/yule/yule-env.<service-id>.conf` (per-service
overrides — e.g. role-specific tokens).

## Operations

```bash
sudo systemctl start yule.target
sudo systemctl restart yule-run-service@eng-role-qa-engineer.service
sudo systemctl status yule-run-service@eng-research-worker.service
journalctl -u yule-run-service@eng-supervisor-watch.service -f
```

## Exit codes

- `0` — graceful shutdown after SIGTERM.
- `78` — `EX_CONFIG`. systemd's `RestartPreventExitStatus=78`
  prevents infinite restart on misconfigured / missing-token state.

## Status / smoke verification

`yule runtime status` (run on the host, no systemd needed) prints a
single-screen diagnostic — per-service health, queue counts, recent
failures, actionable warnings (each STALE/UNKNOWN warning embeds the
exact `systemctl restart …` / `yule run-service …` / `yule runtime up`
command), and a 6-step live smoke checklist.

```bash
yule runtime status --profile engineering
yule runtime status --profile engineering --json
yule runtime status --post-discord     # mirror to #봇-상태 (idempotent)
```

When a worker is restarted via systemd the status output will reflect
the heartbeat update on the next `runtime status` call (within
heartbeat_deadline_seconds — default 90s).

## See also

- `docs/operations.md` §0.1 for the runtime-up vs. discord-up decision
  tree and the canonical service catalog.
- `docs/discord.md` §4 for the dev-only `yule discord up` contract.
- `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` for
  per-worker behaviour contracts.
