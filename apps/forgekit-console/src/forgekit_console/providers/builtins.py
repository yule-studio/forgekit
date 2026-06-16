"""Built-in provider specs — the four shipped providers.

Capability flags project ``docs/provider-capability-matrix.md`` onto each
provider's strongest role:

  * Claude — safety / synthesis / verification平面 → synthesis + safety primary.
  * Codex  — execution / tool 조작 평면 → execution + tool_use primary.
  * Gemini — research / large-context 평면 → research + long_context primary.
  * Ollama — local inference backend → cheap + local + classification.

These are declared once here so registry / policy / tests share one source.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .contract import (
    AUTH_API_KEY,
    AUTH_NONE,
    AUTH_OAUTH,
    CAP_CHAT,
    CAP_CHEAP,
    CAP_CLASSIFICATION,
    CAP_EXECUTION,
    CAP_LOCAL,
    CAP_LONG_CONTEXT,
    CAP_RESEARCH,
    CAP_SAFETY,
    CAP_SYNTHESIS,
    CAP_TOOL_USE,
    HEALTH_API_KEY_SET,
    HEALTH_CLI_PRESENT,
    HEALTH_ENDPOINT_REACHABLE,
    KIND_CLOUD_API,
    KIND_CLOUD_CLI,
    KIND_LOCAL,
    SUBMIT_CLI,
    SUBMIT_OPENAI,
    USAGE_API,
    USAGE_LOCAL,
    USAGE_SUBSCRIPTION,
    ProviderSpec,
)

CLAUDE = ProviderSpec(
    id="claude",
    label="Claude Code",
    kind=KIND_CLOUD_CLI,
    auth_kind=AUTH_OAUTH,
    usage_mode=USAGE_SUBSCRIPTION,
    submit_compat=SUBMIT_CLI,
    health_contract=HEALTH_CLI_PRESENT,
    capability_flags=(CAP_CHAT, CAP_SYNTHESIS, CAP_SAFETY, CAP_TOOL_USE, CAP_LONG_CONTEXT),
)

CODEX = ProviderSpec(
    id="codex",
    label="Codex",
    kind=KIND_CLOUD_CLI,
    auth_kind=AUTH_API_KEY,
    usage_mode=USAGE_API,
    submit_compat=SUBMIT_CLI,
    health_contract=HEALTH_CLI_PRESENT,
    capability_flags=(CAP_CHAT, CAP_EXECUTION, CAP_TOOL_USE),
)

GEMINI = ProviderSpec(
    id="gemini",
    label="Gemini",
    kind=KIND_CLOUD_API,
    auth_kind=AUTH_API_KEY,
    usage_mode=USAGE_API,
    submit_compat=SUBMIT_OPENAI,
    health_contract=HEALTH_API_KEY_SET,
    capability_flags=(CAP_CHAT, CAP_RESEARCH, CAP_LONG_CONTEXT, CAP_CHEAP),
)

OLLAMA = ProviderSpec(
    id="ollama",
    label="Ollama",
    kind=KIND_LOCAL,
    auth_kind=AUTH_NONE,
    usage_mode=USAGE_LOCAL,
    submit_compat=SUBMIT_OPENAI,
    health_contract=HEALTH_ENDPOINT_REACHABLE,
    capability_flags=(CAP_CHAT, CAP_CHEAP, CAP_LOCAL, CAP_CLASSIFICATION),
    endpoint="http://localhost:11434",
)

BUILTIN_PROVIDERS: Dict[str, ProviderSpec] = {
    CLAUDE.id: CLAUDE,
    CODEX.id: CODEX,
    GEMINI.id: GEMINI,
    OLLAMA.id: OLLAMA,
}

BUILTIN_IDS: Tuple[str, ...] = tuple(BUILTIN_PROVIDERS.keys())


def builtin(provider_id: str) -> ProviderSpec | None:
    """Look up a built-in spec by id (None if not a built-in)."""

    return BUILTIN_PROVIDERS.get(provider_id)


def is_builtin(provider_id: str) -> bool:
    return provider_id in BUILTIN_PROVIDERS


__all__ = (
    "CLAUDE", "CODEX", "GEMINI", "OLLAMA",
    "BUILTIN_PROVIDERS", "BUILTIN_IDS",
    "builtin", "is_builtin",
)
