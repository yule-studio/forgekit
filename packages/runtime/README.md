# yule-runtime

Cleanly-movable **runtime primitives** extracted from
`yule_orchestrator.runtime`. The package holds the low-coupling building
blocks that the always-on engineering orchestrator stands on, without
dragging in agent / discord / memory internals.

## Responsibility

Python package: `yule_runtime`.

| Module | Responsibility |
| --- | --- |
| `circuit_breaker` | In-process restart circuit breaker (sliding-window policy) + optional SQLite persistence for cross-process visibility. |
| `services` | The service-manifest inventory: `ServiceKind`, `ServiceSpec`, `PROFILES`, `list_services`, `resolve_service`. |
| `subprocess_supervisor` | The supervisor parent-process restart loop that `yule runtime up` runs in dev / single-host. Depends only on its sibling `circuit_breaker` + `services`. |

## Dependency rule

`yule_runtime` must **NOT** import:

- specific agent internals (`yule_orchestrator.agents.*`)
- discord internals (`yule_orchestrator.discord.*`)
- memory internals (`yule_orchestrator.memory.*`)

It depends only on the Python standard library and on its own sibling
modules. Any module that violates this rule **stays** in
`yule_orchestrator.runtime` (see the TODO list below). This keeps the
package importable in isolation and prevents the runtime layer from
re-coupling to the orchestrator's domain layers.

## Compatibility shims

Each moved module is replaced at its old path
(`src/yule_orchestrator/runtime/<name>.py`) by a thin shim that aliases
`sys.modules[old] = yule_runtime.<name>`. The shim and the real module
are therefore the **same module object**, so:

- every existing `from yule_orchestrator.runtime.X import ...` keeps
  resolving to identical objects, and
- tests that monkeypatch module globals (e.g. `services.PROFILES`) still
  mutate the object the live code reads from.

Shim paths and preserved public names:

- `yule_orchestrator/runtime/circuit_breaker.py` →
  `yule_runtime.circuit_breaker`
  (`CircuitBreakerPolicy`, `CircuitBreakerState`, `CircuitSnapshot`,
  `CircuitBreakerRegistry`, `CircuitBreakerPersistence`,
  `PersistedCircuitRow`, `load_persisted_circuit_snapshots`,
  `DEFAULT_CIRCUIT_WINDOW_SECONDS`, `DEFAULT_CIRCUIT_MAX_RESTARTS`)
- `yule_orchestrator/runtime/services.py` → `yule_runtime.services`
  (`ServiceKind`, `ServiceSpec`, `PROFILES`, `ENGINEERING_PROFILE`,
  `list_services`, `resolve_service`, `build_engineering_profile`,
  `is_coding_executor_autospawn_enabled`,
  `ENV_CODING_EXECUTOR_AUTOSPAWN`)
- `yule_orchestrator/runtime/subprocess_supervisor.py` →
  `yule_runtime.subprocess_supervisor` (supervisor restart loop +
  `run_runtime_up` and friends)

## NOT moved (deliberately left — coupling)

These runtime modules stay in `yule_orchestrator.runtime` because they
import agent / discord / memory internals or are too large + tangled to
move safely under this conservative refactor:

- `status.py` (2083 LOC) — tangled with discord + agent status surfaces;
  explicitly out of scope (do not move / do not split).
- `heartbeats.py` — imports `..agents.job_queue` (`HeartbeatStore`);
  violates the agent-internals dependency rule.
- `run_service.py` — single-worker entrypoint; imports discord runners,
  agent workers, and memory writers.
- `coding_executor_runner.py` — live coding-executor worker; agent +
  external-integration coupling.
- `discord_runner.py` — discord gateway runner; discord internals.
- `work_order_executor_runner.py` — GitHub work-order worker; agent +
  integration coupling.
- `status_poster.py` / `status_summary.py` / `status_cli.py` /
  `circuit_cli.py` / `self_improvement_status.py` — status/CLI surfaces
  that depend on `status.py` and discord; the CLI surface is kept in
  place on purpose so `yule runtime up/status/down` keeps working
  through the shims.
- `fallback.py` / `gateway_env.py` — left for a later pass; not part of
  the low-coupling primitive set targeted here.

## Tests

`packages/runtime/tests/` holds smoke tests for the moved primitives
(circuit-breaker open/close transitions + window aging, service-manifest
parse / resolve, supervisor sibling wiring, and shim object-identity).
