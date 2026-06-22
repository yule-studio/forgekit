"""Per-provider daily token budget — honest enforcement seam (no fake).

The global budget (:mod:`forgekit_provider.usage.budget`) caps the whole day; this caps
**each provider** so one brain can't burn the entire budget and an operator can ring-fence
a paid provider (e.g. gemini 50k/day, ollama unbounded). It is pure: given the config's
``budget_policy.provider_daily_limits`` + today's ledger rows, it answers "is provider X over
its own daily limit?" — consumed as a routing **availability** gate (over → honest fallback to
the next candidate, never a faked send) and surfaced per-provider.

``0`` / absent limit = **unbounded** (honest — an unconfigured provider is never throttled,
and we never invent a limit). Lives next to the usage ledger/breakdown it reads.

Pure / stdlib-only → unit-testable with in-memory rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

# config shape: ``budget_policy: {"provider_daily_limits": {"gemini": 50000, "ollama": 0}}``
BUDGET_POLICY_KEY = "budget_policy"
PROVIDER_LIMITS_KEY = "provider_daily_limits"


def provider_limits(config: Optional[Mapping]) -> Dict[str, int]:
    """Per-provider daily token limits from config. Only positive limits are kept
    (0 / absent / invalid = unbounded → omitted from the map)."""

    pol = (config or {}).get(BUDGET_POLICY_KEY) or {}
    raw = pol.get(PROVIDER_LIMITS_KEY) if isinstance(pol, Mapping) else None
    out: Dict[str, int] = {}
    if isinstance(raw, Mapping):
        for pid, lim in raw.items():
            try:
                v = int(lim)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[str(pid)] = v
    return out


def provider_spent(rows: Sequence[Mapping], pid: str) -> int:
    """Sum ``total_tokens`` for *pid* across *rows* (rows should already be day-scoped).

    Only successful, non-throttled submits count toward spend — a held/throttled attempt
    burned nothing, so counting it would fake a cost."""

    total = 0
    for r in rows or ():
        if str(r.get("provider", "")) != pid:
            continue
        if r.get("throttled"):
            continue
        try:
            total += int(r.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def is_over(pid: str, limits: Mapping[str, int], rows: Sequence[Mapping]) -> bool:
    """True iff *pid* has a positive limit AND today's spend already reached it."""

    limit = limits.get(pid, 0)
    return limit > 0 and provider_spent(rows, pid) >= limit


def over_budget_providers(config: Optional[Mapping], rows: Sequence[Mapping]) -> FrozenSet[str]:
    """The set of providers that are over their per-provider daily limit (honest)."""

    limits = provider_limits(config)
    return frozenset(pid for pid in limits if is_over(pid, limits, rows))


def availability(config: Optional[Mapping], rows: Sequence[Mapping]) -> Callable[[str], bool]:
    """An ``available(pid)`` callable for :func:`routing.resolve_routing` — False for any
    provider over its per-provider budget (so routing honestly falls back to the next)."""

    over = over_budget_providers(config, rows)
    return lambda pid: pid not in over


@dataclass(frozen=True)
class ProviderBudgetState:
    provider: str
    limit: int            # 0 = unbounded
    spent: int
    over: bool

    @property
    def ratio(self) -> float:
        return (self.spent / self.limit) if self.limit > 0 else 0.0

    def to_dict(self) -> dict:
        return {"provider": self.provider, "limit": self.limit, "spent": self.spent,
                "over": self.over, "ratio": round(self.ratio, 3)}


def provider_budget_states(config: Optional[Mapping],
                           rows: Sequence[Mapping]) -> Tuple[ProviderBudgetState, ...]:
    """Honest per-provider budget state for every provider that HAS a limit configured."""

    limits = provider_limits(config)
    return tuple(
        ProviderBudgetState(pid, limit, provider_spent(rows, pid),
                            provider_spent(rows, pid) >= limit)
        for pid, limit in sorted(limits.items())
    )


def limit_lines(config: Optional[Mapping]) -> Tuple[str, ...]:
    """`/provider` 표면 — 설정된 per-provider 한도 (live spend 없이 cfg 만, router 비의존)."""

    limits = provider_limits(config)
    if not limits:
        return ("  per-provider budget: 미설정 (전 provider unbounded — global budget 만 적용)",)
    items = ", ".join(f"{pid}={lim}tok/day" for pid, lim in sorted(limits.items()))
    return (f"  per-provider budget: {items} (초과 시 routing 이 정직하게 fallback/거부)",)


__all__ = (
    "BUDGET_POLICY_KEY", "PROVIDER_LIMITS_KEY",
    "provider_limits", "provider_spent", "is_over", "over_budget_providers",
    "availability", "ProviderBudgetState", "provider_budget_states", "limit_lines",
)
