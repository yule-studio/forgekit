"""Provider policy — resolve which provider fills each agent slot.

Forgekit picks a *main provider* at setup. The policy mode then decides how the
remaining work slots are filled:

  * ``strict-single`` — every slot is the main provider. Simplest, one bill, no
    surprises. Good default when you only have one provider configured.
  * ``hybrid`` — main provider everywhere, but the operator may pin specific
    slots to other *explicitly chosen* providers (no auto-magic).
  * ``optimized`` — like hybrid, plus: for a slot whose capability another
    available provider serves better, auto-pick that provider. Operator overrides
    still win; the main provider is always the deterministic fallback.

Slots are the coarse work types an agent does. Resolution is pure + deterministic
so the same inputs always yield the same mapping (auditable).
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Tuple

from ..providers import builtins
from ..providers.contract import (
    CAP_CHEAP,
    CAP_EXECUTION,
    CAP_LONG_CONTEXT,
    CAP_RESEARCH,
    CAP_SYNTHESIS,
    ProviderSpec,
)

POLICY_STRICT_SINGLE = "strict-single"
POLICY_HYBRID = "hybrid"
POLICY_OPTIMIZED = "optimized"
ALL_POLICIES: Tuple[str, ...] = (POLICY_STRICT_SINGLE, POLICY_HYBRID, POLICY_OPTIMIZED)

SLOT_DEFAULT_CHAT = "default_chat"
SLOT_EXECUTION = "execution"
SLOT_RESEARCH = "research"
SLOT_SYNTHESIS = "synthesis"
SLOT_FALLBACK = "fallback"
ALL_SLOTS: Tuple[str, ...] = (
    SLOT_DEFAULT_CHAT,
    SLOT_EXECUTION,
    SLOT_RESEARCH,
    SLOT_SYNTHESIS,
    SLOT_FALLBACK,
)

# For optimized mode: which capability flag makes a provider "better" for a slot.
_SLOT_CAPABILITY = {
    SLOT_EXECUTION: CAP_EXECUTION,
    SLOT_RESEARCH: CAP_RESEARCH,
    SLOT_SYNTHESIS: CAP_SYNTHESIS,
    # fallback prefers the cheapest available provider when optimizing.
    SLOT_FALLBACK: CAP_CHEAP,
    # default_chat has no single "better" capability — stays on main.
}
# Secondary preference for research → long_context if no research provider.
_SLOT_SECONDARY = {SLOT_RESEARCH: CAP_LONG_CONTEXT}


def _resolve_available(available: Iterable) -> Dict[str, ProviderSpec]:
    """Normalize ``available`` (ids or ProviderSpecs) → {id: ProviderSpec}."""

    out: Dict[str, ProviderSpec] = {}
    for item in available:
        if isinstance(item, ProviderSpec):
            out[item.id] = item
        else:
            spec = builtins.builtin(str(item))
            if spec is not None:
                out[str(item)] = spec
            else:
                out[str(item)] = None  # type: ignore[assignment]
    return out


def _auto_pick(slot: str, main_provider: str, avail: Mapping[str, ProviderSpec]) -> str:
    """Pick the best available provider for *slot* by capability (optimized only).

    Falls back to the main provider when no available provider serves the slot's
    capability better. Deterministic: scans availability in iteration order.
    """

    wanted = _SLOT_CAPABILITY.get(slot)
    if wanted is None:
        return main_provider
    for cap in (wanted, _SLOT_SECONDARY.get(slot)):
        if cap is None:
            continue
        for pid, spec in avail.items():
            if spec is not None and spec.has_capability(cap):
                return pid
    return main_provider


def resolve_slots(
    main_provider: str,
    mode: str,
    *,
    overrides: Mapping[str, str] | None = None,
    available: Iterable = (),
) -> Dict[str, str]:
    """Resolve every slot → provider id for *main_provider* under *mode*.

    * strict-single ignores overrides; every slot is the main provider.
    * hybrid honours explicit overrides; everything else is the main provider.
    * optimized honours overrides, then auto-picks by capability, else main.

    An override (or auto-pick) that names a provider not in *available* is
    rejected and falls back to the main provider — deterministic, never silent
    about an unavailable choice (the result simply holds the main id).
    """

    if mode not in ALL_POLICIES:
        raise ValueError(f"알 수 없는 policy mode: {mode!r}")

    overrides = overrides or {}
    avail = _resolve_available(available)
    # The main provider is always considered available to itself.
    avail.setdefault(main_provider, builtins.builtin(main_provider))  # type: ignore[arg-type]

    def usable(pid: str) -> bool:
        return pid == main_provider or pid in avail

    result: Dict[str, str] = {}
    for slot in ALL_SLOTS:
        if mode == POLICY_STRICT_SINGLE:
            result[slot] = main_provider
            continue

        # hybrid + optimized: explicit override first.
        chosen = overrides.get(slot)
        if chosen and usable(chosen):
            result[slot] = chosen
            continue

        if mode == POLICY_OPTIMIZED:
            picked = _auto_pick(slot, main_provider, avail)
            result[slot] = picked if usable(picked) else main_provider
        else:  # hybrid, no usable override → main
            result[slot] = main_provider

    return result


__all__ = (
    "POLICY_STRICT_SINGLE", "POLICY_HYBRID", "POLICY_OPTIMIZED", "ALL_POLICIES",
    "SLOT_DEFAULT_CHAT", "SLOT_EXECUTION", "SLOT_RESEARCH", "SLOT_SYNTHESIS",
    "SLOT_FALLBACK", "ALL_SLOTS",
    "resolve_slots",
)
