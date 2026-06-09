# yule-agent-contracts

Shared **wire/interop contracts** for the Yule Studio agent platform. These are
the types that `apps/*` and other `packages/*` use to talk to each other and to
the Agent Town front-end.

## Models

| Model | Purpose |
| --- | --- |
| `AgentMessage` (+ `RequestedAction`, `Priority`, `ContextRef`) | Inter-member message protocol with request/reply/close round-trip helpers. |
| `AgentRole` | Structured `<agent>/<role>` identity (round-trips to/from the address strings the protocol carries). |
| `TaskRef` | Pointer to a task / issue / work item (session/brief id and/or repo + number). |
| `AgentCommand` | Envelope for an instruction directed *into* an agent. |
| `AgentEvent` | Envelope for something that *happened* inside an agent. |
| `AgentStatus` | Coarse lifecycle state (`idle` / `running` / `blocked` / …). |

## Dependency rule

This package depends on the **standard library only**. It MUST NOT import
`yule_orchestrator` (the app) or any `apps/*` code — the arrow always points the
other way (`app → contracts`). This keeps the contracts importable from any
side (backend runtime, Discord gateway, Agent Town UI bindings) without dragging
in app internals.

## Relationship to existing app types

`AgentMessage` and friends were **relocated here** from
`yule_orchestrator.agents.messaging.message`; that module is now a thin
compatibility shim re-exporting from `yule_agent_contracts.messages`, so existing
imports keep working.

The other five models are **thin contracts** that sit alongside the heavyweight
in-process domain types rather than replacing them:

| Contract | Heavyweight domain type (stays put) |
| --- | --- |
| `AgentCommand` | `agents.job_queue.store.Job` (`job_type` + `payload`) |
| `AgentEvent` | `agents.job_queue.completion_hook.JobCompletionEvent` |
| `AgentStatus` | `SessionStatusReport` / `LifecycleStatus` / `ServiceStatus` |
| `AgentRole` | `agents.role_profiles.RoleProfile` (mission/responsibilities engine) |
| `TaskRef` | `agents.job_queue.store.Job` / `council.TaskBrief` |

## Install / test

The package is discovered by the root `pyproject.toml`
(`[tool.setuptools.packages.find] where`), so `pip install -e .` at the repo root
puts `yule_agent_contracts` on the path. It also ships its own `pyproject.toml`
for standalone builds.

```bash
# from repo root, with the project venv active
pytest packages/agent-contracts/tests
```
