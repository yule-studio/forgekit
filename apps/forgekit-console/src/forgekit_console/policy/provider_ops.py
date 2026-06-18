"""Provider control-plane operations — edit + persist the brain config (operator UX).

Pure config transforms (a raw dict → a new raw dict) so `/provider use|link|unlink|route`
and the setup flow are unit-testable without IO, plus a thin persist/load pair that
writes ``~/.forgekit/config.json`` (the only IO, path-injectable). Also a ``brain_map``
operator view (declared routing + live-capable vs unsupported providers) and a
``setup_review`` that distinguishes incomplete / misconfigured / missing-auth honestly.

No fake state: a provider with no console transport is shown ``unsupported_in_console``,
not hidden; an invalid config surfaces its errors rather than silently "working".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from ..providers import builtins
from ..runtime_paths import config_path
from . import provider_config as pc
from . import routing as rt

# setup review verdicts
REVIEW_READY = "ready"
REVIEW_INCOMPLETE = "incomplete"          # no primary / no linked
REVIEW_MISCONFIGURED = "misconfigured"    # invalid slot target / unknown provider
REVIEW_NO_LIVE = "no_live_submit"         # configured but nothing console-live


# --- pure config transforms (raw dict → new raw dict) -----------------------
def _clone(cfg: Optional[Mapping]) -> dict:
    return json.loads(json.dumps(dict(cfg or {})))


def set_primary(cfg: Optional[Mapping], pid: str) -> dict:
    out = _clone(cfg)
    out["primary_provider"] = pid
    linked = list(out.get("linked_providers") or [])
    if pid and pid not in linked:
        linked.insert(0, pid)
    out["linked_providers"] = linked
    return out


def link_provider(cfg: Optional[Mapping], pid: str) -> dict:
    out = _clone(cfg)
    linked = list(out.get("linked_providers") or [])
    if pid and pid not in linked:
        linked.append(pid)
    out["linked_providers"] = linked
    return out


def unlink_provider(cfg: Optional[Mapping], pid: str) -> dict:
    out = _clone(cfg)
    linked = [p for p in (out.get("linked_providers") or []) if p != pid]
    out["linked_providers"] = linked
    # drop slot routes that pointed at the removed provider (no dangling targets).
    routes = {s: t for s, t in (out.get("slot_routing") or {}).items() if t != pid}
    out["slot_routing"] = routes
    return out


def route_slot(cfg: Optional[Mapping], slot: str, pid: str) -> dict:
    out = _clone(cfg)
    routes = dict(out.get("slot_routing") or {})
    routes[slot] = pid
    out["slot_routing"] = routes
    return out


def set_implicit_fallback(cfg: Optional[Mapping], enabled: bool) -> dict:
    out = _clone(cfg)
    fb = dict(out.get("fallback_policy") or {})
    fb["implicit_local_fallback"] = bool(enabled)
    out["fallback_policy"] = fb
    return out


# --- persistence (the only IO; path-injectable) -----------------------------
def load_raw_config(*, env: Optional[Mapping[str, str]] = None, path: Optional[Path] = None) -> dict:
    p = path or config_path(env)
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def persist_config(cfg: Mapping, *, env: Optional[Mapping[str, str]] = None,
                   path: Optional[Path] = None) -> Tuple[bool, str]:
    """Validate then write the config. Refuses to persist an invalid brain config."""

    parsed = pc.load_provider_config(cfg)
    errors = pc.validate_provider_config(parsed, config=cfg)
    if errors:
        return False, "; ".join(errors)
    p = path or config_path(env)
    try:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps(dict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")
        return True, str(p)
    except OSError as exc:
        return False, f"write 실패: {exc}"


# --- operator views ---------------------------------------------------------
@dataclass(frozen=True)
class BrainMap:
    primary: str
    linked: Tuple[str, ...]
    slot_targets: Mapping[str, str]
    live_capable: Tuple[str, ...]        # linked providers with a console transport
    unsupported: Tuple[str, ...]         # linked but routable-only (no console submit)

    def to_dict(self) -> dict:
        return {"primary": self.primary, "linked": list(self.linked),
                "slot_targets": dict(self.slot_targets),
                "live_capable": list(self.live_capable), "unsupported": list(self.unsupported)}


def brain_map(parsed: pc.ProviderConfig) -> BrainMap:
    """The declared brain — slot targets + which linked providers are console-live."""

    live, unsup = [], []
    for pid in parsed.linked_providers:
        spec = builtins.builtin(pid)
        (live if rt.submit_supported(spec) else unsup).append(pid)
    targets = {slot: parsed.slot_target(slot) for slot in pc.ROUTING_SLOTS}
    return BrainMap(parsed.primary_provider, parsed.linked_providers, targets,
                    tuple(live), tuple(unsup))


@dataclass(frozen=True)
class SetupReview:
    verdict: str
    issues: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.verdict == REVIEW_READY


def setup_review(cfg: Optional[Mapping]) -> SetupReview:
    """Distinguish incomplete / misconfigured / no-live / ready (honest, no fake-ready)."""

    if not pc.has_brain_config(cfg):
        return SetupReview(REVIEW_INCOMPLETE, ("primary provider 미설정 — `/setup` 으로 브레인을 구성하세요",))
    parsed = pc.load_provider_config(cfg)
    errors = pc.validate_provider_config(parsed, config=cfg)
    if errors:
        return SetupReview(REVIEW_MISCONFIGURED, errors)
    bmap = brain_map(parsed)
    if not bmap.live_capable:
        return SetupReview(REVIEW_NO_LIVE, (
            f"linked provider 가 모두 console live-submit 미지원: {', '.join(bmap.unsupported) or '-'} "
            "— openai-compatible(ollama/gemini)를 link 하거나 fallback 을 설정하세요",))
    return SetupReview(REVIEW_READY, ())


__all__ = (
    "REVIEW_READY", "REVIEW_INCOMPLETE", "REVIEW_MISCONFIGURED", "REVIEW_NO_LIVE",
    "set_primary", "link_provider", "unlink_provider", "route_slot", "set_implicit_fallback",
    "load_raw_config", "persist_config", "BrainMap", "brain_map", "SetupReview", "setup_review",
)
