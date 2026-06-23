"""Provider lane resolution for a runtime tick — brain vs actual transport vs fallback.

The always-on loop routes its autonomous work through a provider. This makes that routing
**first-class and honest** for each tick, distinguishing three things the operator must not
confuse:

  * **brain** — the *declared* provider for the work slot (the routing participant). A
    CLI-only brain (claude / codex) is a meaningful participant even though it has no
    console live transport — it still steers which model the lane reasons with.
  * **actual transport** — who would REALLY answer a console live-submit. Only an
    openai-compatible transport (gemini / ollama) is live; named honestly.
  * **fallback** — whether the declared brain was unusable and an explicit fallback took
    over (and the chain that was tried).

It is a thin read over ``forgekit_provider.policy.routing`` (no new routing engine) — the
ponytail-disciplined choice: surface the existing resolution as a tick attribute, don't
reimplement it. ``unsupported_in_console`` is reported as a real *participant*, never faked
into a live lane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from forgekit_provider.policy import provider_config as pc
from forgekit_provider.policy import routing as rt

# transport kind — what the actual provider can really do in the console.
TRANSPORT_LIVE = "live"                 # actual provider has console live transport (gemini/ollama)
TRANSPORT_PARTICIPANT = "participant"   # brain participant only, unsupported_in_console (claude/codex)
TRANSPORT_NONE = "none"                 # no provider configured
TRANSPORT_KINDS = (TRANSPORT_LIVE, TRANSPORT_PARTICIPANT, TRANSPORT_NONE)


@dataclass(frozen=True)
class TickProviderLane:
    """The provider lane one tick routed through — honest brain/transport/fallback split."""

    slot: str
    brain: str                         # declared provider (routing participant)
    actual_transport: str              # actual live-transport provider ('' if none live)
    transport_kind: str                # TRANSPORT_*
    fallback_used: bool = False
    fallback_chain: Tuple[str, ...] = field(default_factory=tuple)
    status: str = ""                   # RESOLVE_* from routing
    reason: str = ""

    @property
    def live(self) -> bool:
        return self.transport_kind == TRANSPORT_LIVE

    def short(self) -> str:
        """A compact lane tag for a heartbeat/summary line."""

        if self.transport_kind == TRANSPORT_NONE:
            return "no-config"
        if self.live:
            tail = f"→{self.actual_transport}(live)"
            return f"{self.brain}{tail}{'·fallback' if self.fallback_used else ''}"
        return f"{self.brain}(participant)"

    def label(self) -> str:
        """A full, honest operator line."""

        if self.transport_kind == TRANSPORT_NONE:
            return "provider 미설정 — 라우팅 참여 없음 (`/setup` 필요)"
        if self.live:
            via = (f"declared '{self.brain}' 불가 → fallback "
                   if self.fallback_used else "")
            return (f"brain={self.brain} · 실 transport={self.actual_transport} "
                    f"(live console transport) {via}".rstrip())
        # participant-only (unsupported_in_console)
        return (f"brain={self.brain} (routing participant · unsupported_in_console — "
                "console live transport 없음, 라우팅 참여로는 유효)")

    def to_dict(self) -> dict:
        return {"slot": self.slot, "brain": self.brain,
                "actual_transport": self.actual_transport, "transport_kind": self.transport_kind,
                "fallback_used": self.fallback_used, "fallback_chain": list(self.fallback_chain),
                "status": self.status, "reason": self.reason, "live": self.live}

    @classmethod
    def from_dict(cls, d: dict) -> "TickProviderLane":
        return cls(slot=d.get("slot", ""), brain=d.get("brain", ""),
                   actual_transport=d.get("actual_transport", ""),
                   transport_kind=d.get("transport_kind", TRANSPORT_NONE),
                   fallback_used=bool(d.get("fallback_used")),
                   fallback_chain=tuple(d.get("fallback_chain", ())),
                   status=d.get("status", ""), reason=d.get("reason", ""))


def resolve_tick_lane(cfg: pc.ProviderConfig, *, slot: str = pc.SLOT_EXECUTION,
                      supported: Optional[Callable[[str], bool]] = None,
                      available: Optional[Callable[[str], bool]] = None) -> TickProviderLane:
    """Resolve the provider lane the tick's autonomous work routes through.

    Defaults to the EXECUTION slot (the autonomous-work slot the always-on loop drives).
    ``supported``/``available`` are injectable (deterministic tests). The brain is always
    named (even when it has no live transport — a participant is honest, not nothing).
    """

    res = rt.resolve_routing(cfg, slot, supported=supported, available=available)
    if res.status == rt.RESOLVE_NO_CONFIG:
        kind, actual = TRANSPORT_NONE, ""
    elif res.is_live_capable:
        kind, actual = TRANSPORT_LIVE, res.actual_provider
    else:
        # routable but no console live transport (claude/codex) → participant, not live.
        kind, actual = TRANSPORT_PARTICIPANT, ""
    return TickProviderLane(
        slot=res.slot, brain=res.declared_provider, actual_transport=actual,
        transport_kind=kind, fallback_used=res.fallback_used,
        fallback_chain=res.fallback_chain, status=res.status, reason=res.reason)


__all__ = ("TRANSPORT_LIVE", "TRANSPORT_PARTICIPANT", "TRANSPORT_NONE", "TRANSPORT_KINDS",
           "TickProviderLane", "resolve_tick_lane")
