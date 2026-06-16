"""The minimal provider contract every forgekit provider conforms to.

Forgekit is provider-agnostic: Claude / Codex / Gemini / Ollama and any
enterprise/internal endpoint are all described by the *same* fixed ``ProviderSpec``
shape. This is the seam that lets policy (slots / usage) reason over providers
without knowing any vendor specifics — pure data, no live submit here.

Design notes (the "why" behind the fields):

  * ``kind`` separates *where the inference runs* (cloud CLI / cloud API / local
    box / enterprise gateway). It drives doctor checks and auth expectations.
  * ``auth_kind`` is orthogonal to kind — a local provider needs no auth, a cloud
    CLI rides an OAuth session, a cloud API needs a key, an enterprise gateway is
    reached by an endpoint contract.
  * ``capability_flags`` are vendor-neutral capability *hints* (the projection of
    ``docs/provider-capability-matrix.md`` onto one provider) so the optimized
    policy can auto-pick a better provider per slot.
  * ``submit_compat`` says *how* you'd eventually talk to it (cli / openai-style /
    custom http / native) — recorded now so the enterprise seam validates even
    though live submit is intentionally not implemented in this work tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# kind — where the inference physically runs.
KIND_CLOUD_CLI = "cloud_cli"
KIND_CLOUD_API = "cloud_api"
KIND_LOCAL = "local"
KIND_ENTERPRISE = "enterprise"
ALL_KINDS: Tuple[str, ...] = (KIND_CLOUD_CLI, KIND_CLOUD_API, KIND_LOCAL, KIND_ENTERPRISE)

# auth_kind — how a caller authenticates.
AUTH_OAUTH = "oauth"
AUTH_API_KEY = "api_key"
AUTH_NONE = "none"
AUTH_ENDPOINT = "endpoint"
ALL_AUTH_KINDS: Tuple[str, ...] = (AUTH_OAUTH, AUTH_API_KEY, AUTH_NONE, AUTH_ENDPOINT)

# submit_compat — the wire shape (recorded; live submit not implemented here).
SUBMIT_CLI = "cli"
SUBMIT_OPENAI = "openai_compatible"
SUBMIT_CUSTOM_HTTP = "custom_http"
SUBMIT_NATIVE = "native"
ALL_SUBMIT_COMPAT: Tuple[str, ...] = (SUBMIT_CLI, SUBMIT_OPENAI, SUBMIT_CUSTOM_HTTP, SUBMIT_NATIVE)

# usage_mode — billing/availability posture (paired with policy.usage_policy).
USAGE_SUBSCRIPTION = "subscription"
USAGE_API = "api"
USAGE_LOCAL = "local"
USAGE_ENTERPRISE = "enterprise"
ALL_USAGE_MODES: Tuple[str, ...] = (USAGE_SUBSCRIPTION, USAGE_API, USAGE_LOCAL, USAGE_ENTERPRISE)

# health_contract — how `forgekit doctor` would probe the provider.
HEALTH_CLI_PRESENT = "cli_present"        # a CLI binary is on PATH / logged in
HEALTH_API_KEY_SET = "api_key_set"        # an API key env/secret is present
HEALTH_ENDPOINT_REACHABLE = "endpoint_reachable"  # a base URL responds
ALL_HEALTH_CONTRACTS: Tuple[str, ...] = (
    HEALTH_CLI_PRESENT,
    HEALTH_API_KEY_SET,
    HEALTH_ENDPOINT_REACHABLE,
)

# Common capability flags (vendor-neutral; a provider may carry any subset).
CAP_CHAT = "chat"
CAP_EXECUTION = "execution"
CAP_RESEARCH = "research"
CAP_SYNTHESIS = "synthesis"
CAP_LONG_CONTEXT = "long_context"
CAP_TOOL_USE = "tool_use"
CAP_CHEAP = "cheap"
CAP_SAFETY = "safety"
CAP_CLASSIFICATION = "classification"
CAP_LOCAL = "local"


@dataclass(frozen=True)
class ProviderSpec:
    """The fixed minimal contract every provider conforms to."""

    id: str
    label: str
    kind: str                      # one of ALL_KINDS
    auth_kind: str                 # one of ALL_AUTH_KINDS
    usage_mode: str                # one of ALL_USAGE_MODES
    submit_compat: str             # one of ALL_SUBMIT_COMPAT
    health_contract: str           # one of ALL_HEALTH_CONTRACTS
    capability_flags: Tuple[str, ...] = ()
    endpoint: str = ""             # required for local/enterprise; empty otherwise
    enterprise: bool = False       # internal/enterprise provider (seam marker)

    def has_capability(self, flag: str) -> bool:
        return flag in self.capability_flags

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "auth_kind": self.auth_kind,
            "usage_mode": self.usage_mode,
            "submit_compat": self.submit_compat,
            "health_contract": self.health_contract,
            "capability_flags": list(self.capability_flags),
            "endpoint": self.endpoint,
            "enterprise": self.enterprise,
        }


def validate_provider_spec(spec: ProviderSpec) -> Tuple[str, ...]:
    """Return a tuple of human-readable errors (empty == valid).

    Checks the enum membership of every field plus cross-field consistency:
    local/enterprise providers must carry an endpoint; an endpoint health
    contract requires an endpoint; none-auth only makes sense for local; the
    usage_mode must be coherent with the kind.
    """

    errors: list[str] = []

    if not spec.id or not spec.id.strip():
        errors.append("id 가 비어 있습니다")
    if not spec.label or not spec.label.strip():
        errors.append("label 이 비어 있습니다")
    if spec.kind not in ALL_KINDS:
        errors.append(f"알 수 없는 kind: {spec.kind!r}")
    if spec.auth_kind not in ALL_AUTH_KINDS:
        errors.append(f"알 수 없는 auth_kind: {spec.auth_kind!r}")
    if spec.usage_mode not in ALL_USAGE_MODES:
        errors.append(f"알 수 없는 usage_mode: {spec.usage_mode!r}")
    if spec.submit_compat not in ALL_SUBMIT_COMPAT:
        errors.append(f"알 수 없는 submit_compat: {spec.submit_compat!r}")
    if spec.health_contract not in ALL_HEALTH_CONTRACTS:
        errors.append(f"알 수 없는 health_contract: {spec.health_contract!r}")

    needs_endpoint = spec.kind in (KIND_LOCAL, KIND_ENTERPRISE)
    if needs_endpoint and not spec.endpoint.strip():
        errors.append(f"{spec.kind} provider 는 endpoint 가 필요합니다")
    if spec.health_contract == HEALTH_ENDPOINT_REACHABLE and not spec.endpoint.strip():
        errors.append("health_contract=endpoint_reachable 는 endpoint 가 필요합니다")

    if spec.auth_kind == AUTH_NONE and spec.kind not in (KIND_LOCAL,):
        errors.append("auth_kind=none 은 local provider 에서만 허용됩니다")
    if spec.auth_kind == AUTH_ENDPOINT and spec.kind != KIND_ENTERPRISE:
        errors.append("auth_kind=endpoint 는 enterprise provider 에서만 허용됩니다")

    # usage_mode ↔ kind coherence.
    coherent = {
        KIND_CLOUD_CLI: {USAGE_SUBSCRIPTION, USAGE_API},
        KIND_CLOUD_API: {USAGE_API, USAGE_SUBSCRIPTION},
        KIND_LOCAL: {USAGE_LOCAL},
        KIND_ENTERPRISE: {USAGE_ENTERPRISE},
    }
    if spec.kind in ALL_KINDS and spec.usage_mode in ALL_USAGE_MODES:
        if spec.usage_mode not in coherent[spec.kind]:
            errors.append(
                f"usage_mode={spec.usage_mode} 는 kind={spec.kind} 와 맞지 않습니다"
            )

    if spec.enterprise and spec.kind != KIND_ENTERPRISE:
        errors.append("enterprise=True 는 kind=enterprise 와 함께여야 합니다")

    return tuple(errors)


__all__ = (
    "ProviderSpec",
    "validate_provider_spec",
    "KIND_CLOUD_CLI", "KIND_CLOUD_API", "KIND_LOCAL", "KIND_ENTERPRISE", "ALL_KINDS",
    "AUTH_OAUTH", "AUTH_API_KEY", "AUTH_NONE", "AUTH_ENDPOINT", "ALL_AUTH_KINDS",
    "SUBMIT_CLI", "SUBMIT_OPENAI", "SUBMIT_CUSTOM_HTTP", "SUBMIT_NATIVE", "ALL_SUBMIT_COMPAT",
    "USAGE_SUBSCRIPTION", "USAGE_API", "USAGE_LOCAL", "USAGE_ENTERPRISE", "ALL_USAGE_MODES",
    "HEALTH_CLI_PRESENT", "HEALTH_API_KEY_SET", "HEALTH_ENDPOINT_REACHABLE", "ALL_HEALTH_CONTRACTS",
    "CAP_CHAT", "CAP_EXECUTION", "CAP_RESEARCH", "CAP_SYNTHESIS", "CAP_LONG_CONTEXT",
    "CAP_TOOL_USE", "CAP_CHEAP", "CAP_SAFETY", "CAP_CLASSIFICATION", "CAP_LOCAL",
)
