"""Adaptive usage / budget policy — structure first, not billing accuracy.

Forgekit doesn't bill — but it must *reason* about usage posture so it can throttle
or defer work before an operator's budget is blown. This declares the policy
shape; real spend numbers are fed in by callers.

Usage modes (how aggressively forgekit spends):

  * ``adaptive`` — spend freely until a reserve threshold, then throttle. Default.
  * ``strict`` — hard stop at budget; no reserve grace.
  * ``subscription_aware`` — treat a subscription as effectively flat; only the
    reserve guards against rate-limit / fair-use cliffs.
  * ``local_first`` — prefer the local/no-cost provider; only spill to paid when
    a slot truly needs it.

Billing modes mirror the provider usage_mode (subscription / api / local /
enterprise) so the policy can pick a sensible default per provider.

The ``reserve`` is a held-back fraction of the budget (0.0–1.0). When remaining
budget drops into the reserve, forgekit throttles rather than spending it — the
reserve is the safety margin that keeps a run from hard-failing mid-task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

USAGE_ADAPTIVE = "adaptive"
USAGE_STRICT = "strict"
USAGE_SUBSCRIPTION_AWARE = "subscription_aware"
USAGE_LOCAL_FIRST = "local_first"
ALL_USAGE_MODES: Tuple[str, ...] = (
    USAGE_ADAPTIVE,
    USAGE_STRICT,
    USAGE_SUBSCRIPTION_AWARE,
    USAGE_LOCAL_FIRST,
)

BILLING_SUBSCRIPTION = "subscription"
BILLING_API = "api"
BILLING_LOCAL = "local"
BILLING_ENTERPRISE = "enterprise"
ALL_BILLING_MODES: Tuple[str, ...] = (
    BILLING_SUBSCRIPTION,
    BILLING_API,
    BILLING_LOCAL,
    BILLING_ENTERPRISE,
)

# Default usage mode per billing posture.
_BILLING_DEFAULT_USAGE = {
    BILLING_SUBSCRIPTION: USAGE_SUBSCRIPTION_AWARE,
    BILLING_API: USAGE_ADAPTIVE,
    BILLING_LOCAL: USAGE_LOCAL_FIRST,
    BILLING_ENTERPRISE: USAGE_ADAPTIVE,
}

# Default reserve fraction per usage mode.
_USAGE_DEFAULT_RESERVE = {
    USAGE_ADAPTIVE: 0.15,
    USAGE_STRICT: 0.0,
    USAGE_SUBSCRIPTION_AWARE: 0.10,
    USAGE_LOCAL_FIRST: 0.05,
}


@dataclass(frozen=True)
class UsagePolicy:
    """A usage/budget posture: how to spend and how much to hold back."""

    usage_mode: str
    billing_mode: str
    reserve: float = 0.15  # fraction (0.0–1.0) of budget held back as safety margin

    def to_dict(self) -> dict:
        return {
            "usage_mode": self.usage_mode,
            "billing_mode": self.billing_mode,
            "reserve": self.reserve,
        }

    def reserve_floor(self, budget: float) -> float:
        """The absolute spend level at which the reserve begins."""

        return budget * (1.0 - max(0.0, min(1.0, self.reserve)))


def default_usage_policy(main_provider: str, billing_mode: str) -> UsagePolicy:
    """Derive a default usage policy from the main provider's billing mode.

    *main_provider* is accepted for future per-provider tuning; today the billing
    mode drives the defaults.
    """

    if billing_mode not in ALL_BILLING_MODES:
        raise ValueError(f"알 수 없는 billing_mode: {billing_mode!r}")
    usage_mode = _BILLING_DEFAULT_USAGE[billing_mode]
    return UsagePolicy(
        usage_mode=usage_mode,
        billing_mode=billing_mode,
        reserve=_USAGE_DEFAULT_RESERVE[usage_mode],
    )


def should_throttle(policy: UsagePolicy, spent: float, budget: float) -> bool:
    """Should forgekit throttle/defer given *spent* against *budget*?

    * strict mode throttles once spend meets or exceeds budget.
    * every other mode throttles once spend crosses into the held-back reserve.
    * a non-positive budget means "unbounded" (local_first / no-cost) → never
      throttle unless explicitly strict.
    """

    if budget <= 0:
        return policy.usage_mode == USAGE_STRICT and spent > 0
    if policy.usage_mode == USAGE_STRICT:
        return spent >= budget
    return spent >= policy.reserve_floor(budget)


__all__ = (
    "USAGE_ADAPTIVE", "USAGE_STRICT", "USAGE_SUBSCRIPTION_AWARE", "USAGE_LOCAL_FIRST",
    "ALL_USAGE_MODES",
    "BILLING_SUBSCRIPTION", "BILLING_API", "BILLING_LOCAL", "BILLING_ENTERPRISE",
    "ALL_BILLING_MODES",
    "UsagePolicy", "default_usage_policy", "should_throttle",
)
