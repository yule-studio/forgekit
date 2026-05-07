# systemd units for the engineering runtime

`yule run-service <id>` is the canonical worker entrypoint. Both
`yule runtime up` (dev parent) and these systemd units invoke the
same command — only the surrounding supervision differs.

## Files

- `yule-run-service@.service` — template unit. `%i` is the service id.
- `yule.target` — umbrella target so `systemctl start yule.target`
  brings up every enabled instance.

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

## See also

- `docs/operations.md` for the full runtime architecture.
- `policies/runtime/agents/engineering-agent/lifecycle-mvp.md` for
  per-worker behaviour contracts.
