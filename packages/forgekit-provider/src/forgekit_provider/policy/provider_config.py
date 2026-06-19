"""Multi-provider config contract (forgekit brain orchestration).

forgekit itself is the brain; providers (claude/codex/gemini/ollama) are capability
suppliers wired into it. This module is the typed, validated SSoT for the operator's
brain configuration — parsed once from the on-disk config dict so policy / routing /
setup all reason over the SAME shape.

The schema (field names chosen to match the repo's snake_case style)::

    {
      "primary_provider": "claude",
      "linked_providers": ["claude", "codex", "gemini", "ollama"],
      "model_overrides": {"claude": "opus-4.1", "ollama": "gemma3:latest"},
      "slot_routing": {"default_chat": "claude", "research": "gemini",
                        "execution": "codex", "compression": "ollama",
                        "classification": "ollama", "safety": "claude",
                        "synthesis": "claude"},
      "fallback_policy": {
        "implicit_local_fallback": false,
        "slot_fallback_orders": {"default_chat": ["claude", "gemini"],
                                  "execution": ["codex"]}
      },
      "budget_policy": {"primary_monthly_limit": "...",
                         "per_provider": {"claude": "...", "ollama": "local"}}
    }

Honesty rails baked in here:
  * ``implicit_local_fallback`` defaults to **False** — forgekit never silently
    routes to a reachable local ollama just because no config exists.
  * a legacy ``{"main_provider": X}`` (or ``{"id": X}``) config is **migrated** to
    ``primary_provider=X`` / ``linked_providers=[X]`` so existing setups don't break.

Pure / stdlib-only → unit-testable in a bare CI install.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

from ..providers import builtins
from ..providers.registry import build_provider

# The canonical brain slots forgekit routes over (superset of the legacy 5).
SLOT_DEFAULT_CHAT = "default_chat"
SLOT_SYNTHESIS = "synthesis"
SLOT_RESEARCH = "research"
SLOT_EXECUTION = "execution"
SLOT_COMPRESSION = "compression"
SLOT_CLASSIFICATION = "classification"
SLOT_SAFETY = "safety"
ROUTING_SLOTS: Tuple[str, ...] = (
    SLOT_DEFAULT_CHAT, SLOT_SYNTHESIS, SLOT_RESEARCH, SLOT_EXECUTION,
    SLOT_COMPRESSION, SLOT_CLASSIFICATION, SLOT_SAFETY,
)


@dataclass(frozen=True)
class ProviderConfig:
    """The operator's brain configuration — typed + validated."""

    primary_provider: str
    linked_providers: Tuple[str, ...]
    model_overrides: Mapping[str, str] = field(default_factory=dict)
    slot_routing: Mapping[str, str] = field(default_factory=dict)
    implicit_local_fallback: bool = False
    slot_fallback_orders: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    budget_policy: Mapping[str, object] = field(default_factory=dict)
    migrated_from_legacy: bool = False

    @property
    def is_multi(self) -> bool:
        return len(self.linked_providers) >= 2

    def slot_target(self, slot: str) -> str:
        """The declared provider for *slot* — explicit routing, else primary."""

        return str(self.slot_routing.get(slot) or self.primary_provider)

    def fallback_order(self, slot: str) -> Tuple[str, ...]:
        """Explicit fallback order for *slot* (empty = no fallback declared)."""

        return tuple(self.slot_fallback_orders.get(slot, ()))

    def model_for(self, provider_id: str) -> str:
        return str(self.model_overrides.get(provider_id, ""))

    def to_dict(self) -> dict:
        return {
            "primary_provider": self.primary_provider,
            "linked_providers": list(self.linked_providers),
            "model_overrides": dict(self.model_overrides),
            "slot_routing": dict(self.slot_routing),
            "fallback_policy": {
                "implicit_local_fallback": self.implicit_local_fallback,
                "slot_fallback_orders": {k: list(v) for k, v in self.slot_fallback_orders.items()},
            },
            "budget_policy": dict(self.budget_policy),
            "migrated_from_legacy": self.migrated_from_legacy,
        }


def has_brain_config(config: Optional[Mapping]) -> bool:
    """True when SOME provider config exists (new or legacy). Mirrors the setup gate.

    NOTE: a reachable local ollama is intentionally NOT a config — forgekit never
    treats "ollama happens to be up" as being configured (implicit-fallback off)."""

    if not config:
        return False
    primary = str(config.get("primary_provider", "") or "").strip()
    legacy = str(config.get("main_provider", "") or config.get("id", "") or "").strip()
    linked = config.get("linked_providers") or config.get("providers") or ()
    return bool(primary or legacy or linked)


def _coerce_str_map(value) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): str(v) for k, v in value.items() if str(k) and str(v)}


def _coerce_order_map(value) -> Dict[str, Tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return {}
    out: Dict[str, Tuple[str, ...]] = {}
    for k, v in value.items():
        if isinstance(v, (list, tuple)):
            out[str(k)] = tuple(str(x) for x in v if str(x))
    return out


def load_provider_config(config: Optional[Mapping]) -> ProviderConfig:
    """Parse the on-disk config dict into a typed :class:`ProviderConfig`.

    Migrates a legacy ``main_provider`` / ``id`` config to the new shape (primary +
    single linked provider, implicit fallback OFF) so old setups keep working.
    """

    config = config or {}
    primary = str(config.get("primary_provider", "") or "").strip()
    linked_raw = config.get("linked_providers")
    migrated = False

    if not primary:
        # legacy migration: main_provider / id → primary + single linked provider.
        legacy = str(config.get("main_provider", "") or config.get("id", "") or "").strip()
        if legacy:
            primary = legacy
            migrated = True

    linked = tuple(str(p) for p in (linked_raw or ()) if str(p))
    if not linked and primary:
        linked = (primary,)
        if linked_raw is None:
            migrated = migrated or True
    # primary must be in linked (self-link).
    if primary and primary not in linked:
        linked = (primary, *linked)

    fb = config.get("fallback_policy") or {}
    implicit = bool(fb.get("implicit_local_fallback", False)) if isinstance(fb, Mapping) else False

    return ProviderConfig(
        primary_provider=primary,
        linked_providers=linked,
        model_overrides=_coerce_str_map(config.get("model_overrides")),
        slot_routing=_coerce_str_map(config.get("slot_routing")),
        implicit_local_fallback=implicit,
        slot_fallback_orders=_coerce_order_map(
            fb.get("slot_fallback_orders") if isinstance(fb, Mapping) else {}),
        budget_policy=dict(config.get("budget_policy") or {})
        if isinstance(config.get("budget_policy"), Mapping) else {},
        migrated_from_legacy=migrated,
    )


def _known_provider(pid: str) -> bool:
    if builtins.is_builtin(pid):
        return True
    return False  # custom providers validated separately via registry/config


def validate_provider_config(cfg: ProviderConfig, *, config: Optional[Mapping] = None
                             ) -> Tuple[str, ...]:
    """Human-readable errors (empty == valid). Enforces the multi-provider invariants."""

    errors = []
    if not cfg.primary_provider:
        errors.append("primary_provider 가 비어 있습니다 — 메인 브레인 provider 를 정하세요")
    if not cfg.linked_providers:
        errors.append("linked_providers 가 비어 있습니다 — 최소 1개 provider 를 연결하세요")
    if cfg.primary_provider and cfg.primary_provider not in cfg.linked_providers:
        errors.append(f"primary_provider({cfg.primary_provider}) 가 linked_providers 에 없습니다")

    # custom providers may be declared in config['providers']; collect their ids.
    custom_ids = set()
    for entry in (config or {}).get("providers", ()) or ():
        if isinstance(entry, Mapping) and entry.get("id"):
            custom_ids.add(str(entry["id"]))
        elif isinstance(entry, str):
            custom_ids.add(entry)

    def known(pid: str) -> bool:
        return _known_provider(pid) or pid in custom_ids

    for pid in cfg.linked_providers:
        if not known(pid):
            errors.append(f"알 수 없는 linked provider: {pid} (built-in 또는 config.providers 에 정의 필요)")

    # slot routing targets must be linked + a known slot.
    for slot, target in cfg.slot_routing.items():
        if slot not in ROUTING_SLOTS:
            errors.append(f"알 수 없는 slot: {slot} (허용: {', '.join(ROUTING_SLOTS)})")
        if target not in cfg.linked_providers:
            errors.append(f"slot '{slot}' 의 target '{target}' 가 linked_providers 에 없습니다")

    # fallback order entries must be linked.
    for slot, order in cfg.slot_fallback_orders.items():
        for pid in order:
            if pid not in cfg.linked_providers:
                errors.append(f"slot '{slot}' fallback 의 '{pid}' 가 linked_providers 에 없습니다")

    return tuple(errors)


__all__ = (
    "SLOT_DEFAULT_CHAT", "SLOT_SYNTHESIS", "SLOT_RESEARCH", "SLOT_EXECUTION",
    "SLOT_COMPRESSION", "SLOT_CLASSIFICATION", "SLOT_SAFETY", "ROUTING_SLOTS",
    "ProviderConfig", "has_brain_config", "load_provider_config", "validate_provider_config",
)
