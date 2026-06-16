"""Provider registry — build a ProviderSpec from generic config + the enterprise seam.

Two ways a provider enters forgekit:

  1. A built-in id (``claude`` / ``codex`` / ``gemini`` / ``ollama``) — resolved
     straight from :mod:`builtins`, optionally with a few overridable fields.
  2. A *generic config dict* describing a custom / enterprise / internal provider.
     This is the **enterprise seam**: ``openai-compatible`` / ``custom-http`` /
     ``internal-enterprise`` config shapes load through the same contract so a
     company can point forgekit at their own gateway. Live submit is NOT
     implemented — the registry validates the shape so setup/doctor can reason
     about it, and a future work tree wires the actual transport.

A config dict looks like::

    {"id": "acme-gw", "label": "Acme Gateway", "shape": "internal-enterprise",
     "endpoint": "https://llm.acme.internal/v1", "auth_kind": "endpoint",
     "capability_flags": ["chat", "execution"]}
"""

from __future__ import annotations

from typing import Mapping, Tuple

from . import builtins
from .contract import (
    ALL_AUTH_KINDS,
    AUTH_API_KEY,
    AUTH_ENDPOINT,
    HEALTH_API_KEY_SET,
    HEALTH_ENDPOINT_REACHABLE,
    KIND_CLOUD_API,
    KIND_ENTERPRISE,
    SUBMIT_CUSTOM_HTTP,
    SUBMIT_OPENAI,
    USAGE_API,
    USAGE_ENTERPRISE,
    ProviderSpec,
    validate_provider_spec,
)

# Generic config shapes for the enterprise / internal seam.
SHAPE_OPENAI_COMPATIBLE = "openai-compatible"
SHAPE_CUSTOM_HTTP = "custom-http"
SHAPE_INTERNAL_ENTERPRISE = "internal-enterprise"
ALL_SHAPES: Tuple[str, ...] = (
    SHAPE_OPENAI_COMPATIBLE,
    SHAPE_CUSTOM_HTTP,
    SHAPE_INTERNAL_ENTERPRISE,
)

# Per-shape defaults — chosen so a minimal config still produces a valid spec.
_SHAPE_DEFAULTS = {
    SHAPE_OPENAI_COMPATIBLE: {
        "kind": KIND_CLOUD_API,
        "auth_kind": AUTH_API_KEY,
        "usage_mode": USAGE_API,
        "submit_compat": SUBMIT_OPENAI,
        "health_contract": HEALTH_API_KEY_SET,
    },
    SHAPE_CUSTOM_HTTP: {
        "kind": KIND_ENTERPRISE,
        "auth_kind": AUTH_ENDPOINT,
        "usage_mode": USAGE_ENTERPRISE,
        "submit_compat": SUBMIT_CUSTOM_HTTP,
        "health_contract": HEALTH_ENDPOINT_REACHABLE,
    },
    SHAPE_INTERNAL_ENTERPRISE: {
        "kind": KIND_ENTERPRISE,
        "auth_kind": AUTH_ENDPOINT,
        "usage_mode": USAGE_ENTERPRISE,
        "submit_compat": SUBMIT_CUSTOM_HTTP,
        "health_contract": HEALTH_ENDPOINT_REACHABLE,
    },
}


class ProviderConfigError(ValueError):
    """Raised when a provider config dict can't be turned into a valid spec."""


def validate_config(config: Mapping[str, object]) -> Tuple[str, ...]:
    """Return human-readable errors for a generic provider config (empty == ok)."""

    errors: list[str] = []
    if not isinstance(config, Mapping):
        return ("config 는 매핑이어야 합니다",)

    provider_id = str(config.get("id", "")).strip()
    if not provider_id:
        errors.append("config.id 가 비어 있습니다")

    # A built-in reference is always valid (no shape needed).
    if builtins.is_builtin(provider_id) and "shape" not in config:
        return tuple(errors)

    shape = config.get("shape")
    if shape is None:
        errors.append("config.shape (또는 built-in id) 가 필요합니다")
    elif shape not in ALL_SHAPES:
        errors.append(f"알 수 없는 shape: {shape!r} (허용: {', '.join(ALL_SHAPES)})")

    if not str(config.get("label", "")).strip():
        errors.append("config.label 이 비어 있습니다")

    # Endpoint-bearing shapes need an endpoint.
    if shape in (SHAPE_CUSTOM_HTTP, SHAPE_INTERNAL_ENTERPRISE, SHAPE_OPENAI_COMPATIBLE):
        if not str(config.get("endpoint", "")).strip():
            errors.append(f"shape={shape} 는 endpoint 가 필요합니다")

    auth = config.get("auth_kind")
    if auth is not None and auth not in ALL_AUTH_KINDS:
        errors.append(f"알 수 없는 auth_kind: {auth!r}")

    flags = config.get("capability_flags", ())
    if flags and not isinstance(flags, (list, tuple)):
        errors.append("capability_flags 는 리스트여야 합니다")

    # If the shape resolves, surface contract-level errors too.
    if not errors and shape in ALL_SHAPES:
        spec = _spec_from_config(config, shape)
        errors.extend(validate_provider_spec(spec))

    return tuple(errors)


def _spec_from_config(config: Mapping[str, object], shape: str) -> ProviderSpec:
    defaults = dict(_SHAPE_DEFAULTS[shape])
    flags = tuple(config.get("capability_flags") or ())
    return ProviderSpec(
        id=str(config["id"]),
        label=str(config.get("label", config["id"])),
        kind=str(config.get("kind", defaults["kind"])),
        auth_kind=str(config.get("auth_kind", defaults["auth_kind"])),
        usage_mode=str(config.get("usage_mode", defaults["usage_mode"])),
        submit_compat=str(config.get("submit_compat", defaults["submit_compat"])),
        health_contract=str(config.get("health_contract", defaults["health_contract"])),
        capability_flags=tuple(str(f) for f in flags),
        endpoint=str(config.get("endpoint", "")),
        enterprise=defaults["kind"] == KIND_ENTERPRISE,
    )


def build_provider(config: Mapping[str, object]) -> ProviderSpec:
    """Build a ProviderSpec from a generic config dict (built-in id or a shape).

    Raises :class:`ProviderConfigError` if the config is invalid.
    """

    errors = validate_config(config)
    if errors:
        raise ProviderConfigError("; ".join(errors))

    provider_id = str(config["id"]).strip()
    if builtins.is_builtin(provider_id) and "shape" not in config:
        base = builtins.BUILTIN_PROVIDERS[provider_id]
        # Allow a couple of overridable fields (label / endpoint / extra flags).
        if not any(k in config for k in ("label", "endpoint", "capability_flags")):
            return base
        from dataclasses import replace

        flags = tuple(config.get("capability_flags") or base.capability_flags)
        return replace(
            base,
            label=str(config.get("label", base.label)),
            endpoint=str(config.get("endpoint", base.endpoint)),
            capability_flags=tuple(str(f) for f in flags),
        )

    return _spec_from_config(config, str(config["shape"]))


def no_provider_configured(config: Mapping[str, object] | None) -> bool:
    """Setup-incomplete signal: no provider has been configured yet.

    True when there is no config at all, no ``main_provider``/``id``, and no
    ``providers`` list — i.e. forgekit setup hasn't picked a provider.
    """

    if not config:
        return True
    main = str(config.get("main_provider", "") or config.get("id", "")).strip()
    providers = config.get("providers") or ()
    return not main and not providers


__all__ = (
    "ProviderConfigError",
    "build_provider",
    "validate_config",
    "no_provider_configured",
    "SHAPE_OPENAI_COMPATIBLE", "SHAPE_CUSTOM_HTTP", "SHAPE_INTERNAL_ENTERPRISE", "ALL_SHAPES",
)
