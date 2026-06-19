"""Main-provider-derived defaults.

The provider you pick as *main* at setup biases forgekit's defaults: the default
policy mode, the agent's lean (what kind of work it's tuned for), the default
usage mode, and any capability warnings. This keeps setup to one decision —
"which provider is yours" — and derives sensible defaults the operator can still
override.

  * claude  → synthesis-heavy lean, hybrid mode, subscription usage.
  * codex   → execution-heavy lean, hybrid mode, api usage.
  * gemini  → research-heavy lean, hybrid mode, api usage.
  * ollama  → local-first lean, strict-single mode, local usage, + a "limited
    capability" warning (a single local model can't cover every slot well).

A custom/enterprise main provider falls back to a neutral profile derived from
its capability flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from ..providers import builtins
from ..providers.contract import (
    CAP_EXECUTION,
    CAP_LOCAL,
    CAP_RESEARCH,
    CAP_SYNTHESIS,
    ProviderSpec,
)
from .provider_policy import POLICY_HYBRID, POLICY_STRICT_SINGLE

LEAN_SYNTHESIS = "synthesis-heavy"
LEAN_EXECUTION = "execution-heavy"
LEAN_RESEARCH = "research-heavy"
LEAN_LOCAL_FIRST = "local-first"
LEAN_BALANCED = "balanced"


@dataclass(frozen=True)
class MainProviderProfile:
    """Defaults derived from the chosen main provider."""

    main_provider: str
    default_policy_mode: str
    agent_lean: str
    default_usage_mode: str
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "main_provider": self.main_provider,
            "default_policy_mode": self.default_policy_mode,
            "agent_lean": self.agent_lean,
            "default_usage_mode": self.default_usage_mode,
            "warnings": list(self.warnings),
        }


# Explicit per-builtin profiles (keeps intent obvious vs. deriving everything).
_BUILTIN_PROFILES = {
    "claude": (POLICY_HYBRID, LEAN_SYNTHESIS),
    "codex": (POLICY_HYBRID, LEAN_EXECUTION),
    "gemini": (POLICY_HYBRID, LEAN_RESEARCH),
    "ollama": (POLICY_STRICT_SINGLE, LEAN_LOCAL_FIRST),
}


def _lean_from_capabilities(spec: ProviderSpec) -> str:
    """Derive a lean for a custom/enterprise provider from its flags."""

    if spec.has_capability(CAP_LOCAL):
        return LEAN_LOCAL_FIRST
    if spec.has_capability(CAP_EXECUTION):
        return LEAN_EXECUTION
    if spec.has_capability(CAP_RESEARCH):
        return LEAN_RESEARCH
    if spec.has_capability(CAP_SYNTHESIS):
        return LEAN_SYNTHESIS
    return LEAN_BALANCED


def profile_for(main_provider_id: str, spec: ProviderSpec | None = None) -> MainProviderProfile:
    """Return the default profile for *main_provider_id*.

    For a built-in, the mapping above wins. For a custom/enterprise provider, pass
    its resolved ``spec`` so the lean/usage can be derived from its flags.
    """

    resolved = spec or builtins.builtin(main_provider_id)

    if main_provider_id in _BUILTIN_PROFILES:
        mode, lean = _BUILTIN_PROFILES[main_provider_id]
        usage = resolved.usage_mode if resolved else "subscription"
        warnings: Tuple[str, ...] = ()
        if main_provider_id == "ollama":
            warnings = (
                "ollama 는 단일 로컬 모델이라 slot 전반 capability 가 제한적입니다 "
                "(execution/research/synthesis 는 cloud provider 보강 권장)",
            )
        return MainProviderProfile(
            main_provider=main_provider_id,
            default_policy_mode=mode,
            agent_lean=lean,
            default_usage_mode=usage,
            warnings=warnings,
        )

    # custom / enterprise — derive from flags.
    if resolved is None:
        return MainProviderProfile(
            main_provider=main_provider_id,
            default_policy_mode=POLICY_STRICT_SINGLE,
            agent_lean=LEAN_BALANCED,
            default_usage_mode="enterprise",
            warnings=("미지의 provider — 보수적으로 strict-single 로 시작합니다",),
        )

    lean = _lean_from_capabilities(resolved)
    warnings = ()
    if lean == LEAN_LOCAL_FIRST:
        warnings = ("local-first provider — capability 가 제한적일 수 있습니다",)
    return MainProviderProfile(
        main_provider=main_provider_id,
        default_policy_mode=POLICY_HYBRID,
        agent_lean=lean,
        default_usage_mode=resolved.usage_mode,
        warnings=warnings,
    )


__all__ = (
    "MainProviderProfile",
    "profile_for",
    "LEAN_SYNTHESIS", "LEAN_EXECUTION", "LEAN_RESEARCH", "LEAN_LOCAL_FIRST", "LEAN_BALANCED",
)
