# yule-core

Pure-leaf platform utilities for Yule Studio agents. These are the
lowest-level building blocks shared across the monolith and every
extracted package: process environment loading, local-timezone helpers,
TLS/CA-bundle fallback, and agent context-document loading. They are
fully deterministic and testable in isolation.

## Responsibility

- **`env_loader`** — `load_env_files`: idempotent `.env` file loading
  into `os.environ` (does not overwrite already-set vars).
- **`timezone`** — `local_tz`, `local_tz_name`, `now_local`, `to_local`:
  resolve the operator's local timezone (via env / zoneinfo) and produce
  timezone-aware datetimes.
- **`tls`** — `TLSCABundle`, `resolve_ca_bundle`,
  `apply_ca_bundle_fallback`: detect an unusable default OpenSSL CA file
  and fall back to a usable bundle so outbound HTTPS keeps working.
- **`context_loader`** — `ContextDocument`, `LoadedContext`,
  `ContextError`, `load_agent_context`, `render_context`: load and render
  per-agent context documents.

## Dependency rule

`yule_core` depends on **the Python standard library only**
(`dependencies = []`). It MUST NOT import `yule_engineering` runtime,
agents, Discord, or any other package — it is a pure leaf at the bottom
of the dependency graph.

`tls` will *optionally* use `certifi` as a CA-bundle fallback **iff** it
is already installed (`import certifi` inside a `try/except
ImportError`); it is intentionally **not** declared as a runtime
dependency, since absence is handled gracefully (the fallback simply
returns `None`).

## Compatibility

`yule_engineering.core.{__init__,context_loader,env_loader,timezone,tls}`
are thin compat shims. The submodule shims alias the moved modules via
`sys.modules[__name__] = <yule_core module>`, preserving object identity
so existing `from yule_engineering.core import ...` imports — and any
monkeypatch/reload in the 13 external importers — keep resolving to the
identical objects.

## Public API

`ContextError`, `ContextDocument`, `LoadedContext`, `TLSCABundle`,
`apply_ca_bundle_fallback`, `load_agent_context`, `load_env_files`,
`local_tz`, `local_tz_name`, `now_local`, `resolve_ca_bundle`,
`render_context`, `to_local`.
