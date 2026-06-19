"""Forgekit provider layer — the vendor-neutral provider contract + registry.

``contract`` holds the fixed ``ProviderSpec`` shape every provider conforms to,
``builtins`` declares the four shipped providers, ``registry`` builds specs from
generic config (the enterprise / internal seam). Pure data + validation; no live
submit lives here.
"""

from __future__ import annotations

from .builtins import BUILTIN_IDS, BUILTIN_PROVIDERS, builtin, is_builtin
from .contract import ProviderSpec, validate_provider_spec
from .registry import (
    ALL_SHAPES,
    ProviderConfigError,
    build_provider,
    no_provider_configured,
    validate_config,
)

__all__ = (
    "ProviderSpec",
    "validate_provider_spec",
    "BUILTIN_PROVIDERS",
    "BUILTIN_IDS",
    "builtin",
    "is_builtin",
    "build_provider",
    "validate_config",
    "no_provider_configured",
    "ProviderConfigError",
    "ALL_SHAPES",
)
