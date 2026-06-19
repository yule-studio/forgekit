"""Forgekit policy layer — provider slot resolution / main-provider defaults / usage.

``provider_policy`` resolves which provider fills each agent slot under a mode
(strict-single / hybrid / optimized); ``main_profile`` derives setup defaults from
the chosen main provider; ``usage_policy`` is the adaptive usage/budget posture
(modes + reserve). Pure rules over the provider contract — no live submit.
"""

from __future__ import annotations

from .main_profile import MainProviderProfile, profile_for
from .provider_policy import (
    ALL_POLICIES,
    ALL_SLOTS,
    POLICY_HYBRID,
    POLICY_OPTIMIZED,
    POLICY_STRICT_SINGLE,
    resolve_slots,
)
from .usage_policy import (
    ALL_BILLING_MODES,
    ALL_USAGE_MODES,
    UsagePolicy,
    default_usage_policy,
    should_throttle,
)

__all__ = (
    "resolve_slots",
    "POLICY_STRICT_SINGLE",
    "POLICY_HYBRID",
    "POLICY_OPTIMIZED",
    "ALL_POLICIES",
    "ALL_SLOTS",
    "MainProviderProfile",
    "profile_for",
    "UsagePolicy",
    "default_usage_policy",
    "should_throttle",
    "ALL_USAGE_MODES",
    "ALL_BILLING_MODES",
)
