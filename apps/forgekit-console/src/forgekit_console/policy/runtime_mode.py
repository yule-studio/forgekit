"""Forgekit runtime MODE — the operator-facing posture that COMPOSES the existing
provider / usage policy seams into one concrete, auditable :class:`EffectivePolicy`.

A *runtime mode* is what the operator switches with Shift+Tab. It is NOT a new
provider system — it is a thin posture layer that decides, per mode, HOW the
existing seams are driven:

* ``provider_policy.resolve_slots`` — which provider fills each work slot (the
  mode picks the provider-policy ``strict-single`` / ``hybrid`` / ``optimized``,
  or defers to the main-provider profile default).
* ``usage_policy`` — how aggressively to spend (the mode biases the usage mode +
  reserve, e.g. cost-save / approval-wait force ``strict``).
* autonomy / approval / background-loop / note-write / budget posture — declared
  per mode so changing the mode produces a *different* resolved policy, not just a
  different label.

So "Shift+Tab changes the mode" means "the resolved EffectivePolicy changes" —
different slot routing, different usage throttle, different approval gate. That is
the real policy change, and it is the same regardless of which provider is main
(vendor-neutral): the mode rules sit on top of the provider contract, never inside
a per-vendor branch.

Pure + dependency-free (only the sibling policy modules), so it is fully unit
testable and runs in a bare CI install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple

from . import provider_policy as pp
from . import usage_policy as up
from .main_profile import MainProviderProfile

# --- autonomy levels (what forgekit may do on its own) ----------------------
AUTONOMY_MANUAL = "manual"      # nothing without an explicit operator action
AUTONOMY_ASSISTED = "assisted"  # acts on request; proposes, operator confirms writes
AUTONOMY_BOUNDED = "bounded"    # may act within hard rails; destructive → approval
AUTONOMY_OBSERVE = "observe"    # read / classify / report only; no mutations

# --- approval posture (which actions wait for the operator) -----------------
APPROVAL_HOLD = "hold-all"            # everything waits (approval-wait)
APPROVAL_ALL_WRITES = "all-writes"    # any write/action waits
APPROVAL_DESTRUCTIVE = "destructive"  # only destructive / deploy / secret waits
APPROVAL_NONE = "none"                # nothing auto-gated (read-only modes)

# --- budget posture (how tight the spend guard is) --------------------------
BUDGET_STRICT = "strict"
BUDGET_NORMAL = "normal"
BUDGET_RELAXED = "relaxed"

# Routing sentinel — defer to the main-provider profile's default policy mode.
ROUTING_FROM_PROFILE = "from-profile"

# Usage bias — how the mode overrides the profile-derived usage mode.
USAGE_BIAS_PROFILE = "from-profile"
USAGE_BIAS_STRICT = "strict"
USAGE_BIAS_ADAPTIVE = "adaptive"

# --- mode ids ---------------------------------------------------------------
MODE_INTERACTIVE = "interactive"
MODE_DELIVERY = "delivery"
MODE_RESEARCH = "research"
MODE_WATCH = "watch"
MODE_ALWAYS_ON = "always-on"
MODE_COST_SAVE = "cost-save"
MODE_APPROVAL_WAIT = "approval-wait"


@dataclass(frozen=True)
class RuntimeMode:
    """A runtime posture — declarative knobs the EffectivePolicy resolves against."""

    id: str
    label: str
    purpose: str
    routing_policy: str        # a provider_policy POLICY_* or ROUTING_FROM_PROFILE
    autonomy: str
    approval: str
    usage_bias: str
    background_loop: bool
    note_write: bool
    budget_posture: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "purpose": self.purpose,
            "routing_policy": self.routing_policy,
            "autonomy": self.autonomy,
            "approval": self.approval,
            "usage_bias": self.usage_bias,
            "background_loop": self.background_loop,
            "note_write": self.note_write,
            "budget_posture": self.budget_posture,
        }


# The ordered registry — Shift+Tab cycles through this tuple.
RUNTIME_MODES: Tuple[RuntimeMode, ...] = (
    RuntimeMode(
        MODE_INTERACTIVE, "Interactive", "operator 가 직접 모는 일반 작업",
        routing_policy=ROUTING_FROM_PROFILE, autonomy=AUTONOMY_ASSISTED,
        approval=APPROVAL_ALL_WRITES, usage_bias=USAGE_BIAS_PROFILE,
        background_loop=False, note_write=True, budget_posture=BUDGET_NORMAL,
    ),
    RuntimeMode(
        MODE_DELIVERY, "Delivery", "한 작업을 실제로 끝까지 밀어붙임",
        routing_policy=pp.POLICY_OPTIMIZED, autonomy=AUTONOMY_BOUNDED,
        approval=APPROVAL_DESTRUCTIVE, usage_bias=USAGE_BIAS_ADAPTIVE,
        background_loop=False, note_write=True, budget_posture=BUDGET_NORMAL,
    ),
    RuntimeMode(
        MODE_RESEARCH, "Research", "탐색 / 조사 — 넓게 보고 정리",
        routing_policy=pp.POLICY_OPTIMIZED, autonomy=AUTONOMY_ASSISTED,
        approval=APPROVAL_ALL_WRITES, usage_bias=USAGE_BIAS_ADAPTIVE,
        background_loop=False, note_write=True, budget_posture=BUDGET_RELAXED,
    ),
    RuntimeMode(
        MODE_WATCH, "Watch", "관측 전용 — 읽고 분류하고 보고만",
        routing_policy=pp.POLICY_STRICT_SINGLE, autonomy=AUTONOMY_OBSERVE,
        approval=APPROVAL_ALL_WRITES, usage_bias=USAGE_BIAS_STRICT,
        background_loop=True, note_write=True, budget_posture=BUDGET_STRICT,
    ),
    RuntimeMode(
        MODE_ALWAYS_ON, "Always-on", "장시간 bounded 운영 — 관측→분류→패킷→위임→대기",
        routing_policy=pp.POLICY_HYBRID, autonomy=AUTONOMY_BOUNDED,
        approval=APPROVAL_DESTRUCTIVE, usage_bias=USAGE_BIAS_ADAPTIVE,
        background_loop=True, note_write=True, budget_posture=BUDGET_STRICT,
    ),
    RuntimeMode(
        MODE_COST_SAVE, "Cost-save", "비용 최소화 — 단일/저가 라우팅, 쓰기 최소",
        routing_policy=pp.POLICY_STRICT_SINGLE, autonomy=AUTONOMY_ASSISTED,
        approval=APPROVAL_ALL_WRITES, usage_bias=USAGE_BIAS_STRICT,
        background_loop=False, note_write=False, budget_posture=BUDGET_STRICT,
    ),
    RuntimeMode(
        MODE_APPROVAL_WAIT, "Approval-wait", "모든 행동 보류 — operator 응답 대기",
        routing_policy=ROUTING_FROM_PROFILE, autonomy=AUTONOMY_MANUAL,
        approval=APPROVAL_HOLD, usage_bias=USAGE_BIAS_STRICT,
        background_loop=False, note_write=False, budget_posture=BUDGET_STRICT,
    ),
)

_MODE_BY_ID: Dict[str, RuntimeMode] = {m.id: m for m in RUNTIME_MODES}
DEFAULT_MODE = MODE_INTERACTIVE


def all_modes() -> Tuple[RuntimeMode, ...]:
    return RUNTIME_MODES


def get_mode(mode_id: str) -> RuntimeMode:
    """Return the mode (falls back to the default for an unknown id)."""

    return _MODE_BY_ID.get(mode_id, _MODE_BY_ID[DEFAULT_MODE])


def cycle_mode(current_id: str, direction: int = 1) -> str:
    """Return the next/previous mode id (wraps). Pure — drives Shift+Tab."""

    ids = [m.id for m in RUNTIME_MODES]
    try:
        idx = ids.index(current_id)
    except ValueError:
        idx = 0
    return ids[(idx + direction) % len(ids)]


@dataclass(frozen=True)
class EffectivePolicy:
    """The CONCRETE policy resolved from (main-provider profile × runtime mode).

    This is what actually changes when the operator cycles modes: the provider
    policy mode, the per-slot routing, the usage/throttle posture, and the
    autonomy / approval / loop / budget knobs — all derived, all auditable.
    """

    mode_id: str
    mode_label: str
    main_provider: str
    provider_policy_mode: str          # resolved strict-single / hybrid / optimized
    slots: Mapping[str, str]           # slot -> provider id (provider_policy.resolve_slots)
    usage: up.UsagePolicy
    autonomy: str
    approval: str
    background_loop: bool
    note_write: bool
    budget_posture: str

    def routing_target(self) -> str:
        """The provider that fills the default chat slot (the live-submit lean)."""

        return self.slots.get(pp.SLOT_DEFAULT_CHAT, self.main_provider)

    def holds_all_actions(self) -> bool:
        return self.approval == APPROVAL_HOLD

    def to_dict(self) -> dict:
        return {
            "mode_id": self.mode_id,
            "mode_label": self.mode_label,
            "main_provider": self.main_provider,
            "provider_policy_mode": self.provider_policy_mode,
            "slots": dict(self.slots),
            "usage": self.usage.to_dict(),
            "autonomy": self.autonomy,
            "approval": self.approval,
            "background_loop": self.background_loop,
            "note_write": self.note_write,
            "budget_posture": self.budget_posture,
        }


def _billing_mode(profile: MainProviderProfile) -> str:
    raw = (profile.default_usage_mode or "").strip()
    return raw if raw in up.ALL_BILLING_MODES else up.BILLING_API


def _usage_for(profile: MainProviderProfile, mode: RuntimeMode) -> up.UsagePolicy:
    base = up.default_usage_policy(profile.main_provider, _billing_mode(profile))
    if mode.usage_bias == USAGE_BIAS_STRICT:
        return up.UsagePolicy(up.USAGE_STRICT, base.billing_mode, reserve=0.0)
    if mode.usage_bias == USAGE_BIAS_ADAPTIVE:
        return up.UsagePolicy(up.USAGE_ADAPTIVE, base.billing_mode, reserve=0.15)
    return base  # from-profile


def resolve_effective_policy(
    profile: MainProviderProfile,
    mode_id: str,
    *,
    overrides: Optional[Mapping[str, str]] = None,
    available: Iterable = (),
) -> EffectivePolicy:
    """Compose (main-provider profile × runtime mode) → a concrete EffectivePolicy.

    The mode picks the provider-policy mode (or defers to the profile default); the
    existing ``resolve_slots`` then yields the per-slot routing, and ``usage_policy``
    the spend posture. Same inputs → same output (auditable, vendor-neutral).
    """

    mode = get_mode(mode_id)
    policy_mode = (
        profile.default_policy_mode
        if mode.routing_policy == ROUTING_FROM_PROFILE
        else mode.routing_policy
    )
    if policy_mode not in pp.ALL_POLICIES:
        policy_mode = pp.POLICY_STRICT_SINGLE
    slots = pp.resolve_slots(
        profile.main_provider, policy_mode, overrides=overrides, available=available
    )
    return EffectivePolicy(
        mode_id=mode.id,
        mode_label=mode.label,
        main_provider=profile.main_provider,
        provider_policy_mode=policy_mode,
        slots=slots,
        usage=_usage_for(profile, mode),
        autonomy=mode.autonomy,
        approval=mode.approval,
        background_loop=mode.background_loop,
        note_write=mode.note_write,
        budget_posture=mode.budget_posture,
    )


__all__ = (
    "AUTONOMY_MANUAL", "AUTONOMY_ASSISTED", "AUTONOMY_BOUNDED", "AUTONOMY_OBSERVE",
    "APPROVAL_HOLD", "APPROVAL_ALL_WRITES", "APPROVAL_DESTRUCTIVE", "APPROVAL_NONE",
    "BUDGET_STRICT", "BUDGET_NORMAL", "BUDGET_RELAXED",
    "ROUTING_FROM_PROFILE",
    "MODE_INTERACTIVE", "MODE_DELIVERY", "MODE_RESEARCH", "MODE_WATCH",
    "MODE_ALWAYS_ON", "MODE_COST_SAVE", "MODE_APPROVAL_WAIT",
    "RuntimeMode", "RUNTIME_MODES", "DEFAULT_MODE",
    "all_modes", "get_mode", "cycle_mode",
    "EffectivePolicy", "resolve_effective_policy",
)
