"""Refresh-plan → provider routing.

Given a :class:`RefreshPlan` from :mod:`.scheduler` and a
:class:`KnowledgeProviderRegistry` from :mod:`.provider_registry`,
this module annotates each entry with the transport, provider, and
availability the orchestrator will dispatch to. It also offers a
small re-ranker that prefers axes the role hasn't covered recently —
so when a tick quota is tight, a chronically-unhealthy axis gets the
slot instead of being starved by an axis that's already fresh.

Strict offline. Pure functions. Tests pin behaviour deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from .models import SourceAxis, SourceEntry, SourceTier
from .provider_registry import (
    KnowledgeProviderRegistration,
    KnowledgeProviderRegistry,
    ProviderAvailability,
    default_registry,
)
from .providers import (
    LiveProviderSpec,
    ProviderTransport,
    provider_spec_for,
)


# ---------------------------------------------------------------------------
# Routed refresh candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutedRefreshCandidate:
    """A due / skipped source enriched with provider routing.

    Bundles four facts the tick runner needs for one source:

      * ``source`` — the registry row (axes / role tags / content
        policy travel with the candidate).
      * ``spec`` — the :class:`LiveProviderSpec` (transport, endpoint,
        parser, rate limit).
      * ``provider`` — the registry row (auth contract, fetcher).
      * ``availability`` — the resolved
        :class:`ProviderAvailability` for the operator's env.

    Plus the original :class:`RefreshPlanEntry` decision so the
    caller can still distinguish ``due`` vs ``skipped_*`` without
    re-running the planner.
    """

    source: SourceEntry
    spec: LiveProviderSpec
    provider: KnowledgeProviderRegistration
    availability: ProviderAvailability
    decision: str
    reason: str
    next_eligible_at: Optional[str] = None

    @property
    def axes(self) -> Tuple[SourceAxis, ...]:
        return self.source.axes

    @property
    def transport(self) -> ProviderTransport:
        return self.spec.transport

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source_id": self.source.source_id,
            "transport": self.transport.value,
            "provider_id": self.provider.provider_id,
            "availability": self.availability.value,
            "decision": self.decision,
            "reason": self.reason,
            "next_eligible_at": self.next_eligible_at,
            "axes": [axis.value for axis in self.axes],
            "auth": self.provider.auth.to_payload(),
        }


# ---------------------------------------------------------------------------
# route_refresh_plan
# ---------------------------------------------------------------------------


def route_refresh_plan(
    plan: Any,
    *,
    role_id: str,
    registry: Optional[KnowledgeProviderRegistry] = None,
    env: Mapping[str, str] = (),  # type: ignore[assignment]
) -> Tuple[
    Tuple[RoutedRefreshCandidate, ...],
    Tuple[RoutedRefreshCandidate, ...],
]:
    """Annotate every entry of *plan* with provider routing.

    Returns ``(due_candidates, skipped_candidates)`` — both tuples of
    :class:`RoutedRefreshCandidate`. Skipped entries also carry the
    transport + availability so a "would have been due but provider
    is blocked" combination is visible without recomputing.

    *registry* defaults to :func:`default_registry` so the simplest
    call site (``route_refresh_plan(plan, role_id=role)``) still
    resolves to the seeded contract.

    Pure function. The orchestrator threads a registry it constructed
    once at startup plus the env mapping it already evaluated for
    other components.
    """

    from .source_registry import role_sources  # local import keeps cycles out

    reg = registry if registry is not None else default_registry()
    sources_by_id = {s.source_id: s for s in role_sources(role_id)}
    env_map: Mapping[str, str] = dict(env or {})

    def _build(entry: Any) -> Optional[RoutedRefreshCandidate]:
        source = sources_by_id.get(entry.source_id)
        if source is None:
            return None
        spec = provider_spec_for(source)
        registration = reg.get(spec.transport)
        availability = registration.evaluate_availability(env_map)
        return RoutedRefreshCandidate(
            source=source,
            spec=spec,
            provider=registration,
            availability=availability,
            decision=entry.decision,
            reason=entry.reason,
            next_eligible_at=getattr(entry, "next_eligible_at", None),
        )

    due_routed = tuple(c for c in (_build(e) for e in plan.due) if c)
    skipped_routed = tuple(c for c in (_build(e) for e in plan.skipped) if c)
    return due_routed, skipped_routed


# ---------------------------------------------------------------------------
# axis_priority_order
# ---------------------------------------------------------------------------


_AVAILABILITY_RANK: Mapping[ProviderAvailability, int] = {
    ProviderAvailability.AVAILABLE: 0,
    ProviderAvailability.NO_LIVE_IMPL: 1,
    ProviderAvailability.MANUAL_ONLY: 2,
    ProviderAvailability.DISABLED_BY_FLAG: 3,
    ProviderAvailability.MISSING_ENV: 4,
}


_TIER_RANK: Mapping[SourceTier, int] = {
    SourceTier.TIER_1: 0,
    SourceTier.TIER_2: 1,
    SourceTier.TIER_3: 2,
    SourceTier.TIER_4: 3,
}


def axis_priority_order(
    candidates: Sequence[RoutedRefreshCandidate],
    *,
    overdue_axes: Sequence[str] = (),
) -> Tuple[RoutedRefreshCandidate, ...]:
    """Re-rank *candidates* so axes that are overdue come first.

    Goal: when tick quota is tight, the slot goes to a source whose
    axis hasn't been refreshed in the longest time. A chronically
    broken axis surfaces fresh items first instead of being starved
    by an already-healthy axis.

    Tie-breakers (after axis health):
      * provider availability — ``AVAILABLE`` first (keep live
        bandwidth hot once it's actually live).
      * source tier — Tier 1 first (officially-trusted wins ties).
      * source_id alphabetical — for determinism in tests / replay.

    Pass :func:`scheduler.overdue_axes_for_role` output directly as
    *overdue_axes* — those are the axis values (string form) the
    planner already considers stale.
    """

    overdue_set = set(overdue_axes)

    def _hits_overdue_axis(c: RoutedRefreshCandidate) -> int:
        # 0 = at least one axis is overdue (rank first), 1 otherwise.
        for axis in c.axes:
            if axis.value in overdue_set:
                return 0
        return 1

    def _key(c: RoutedRefreshCandidate) -> Tuple[int, int, int, str]:
        return (
            _hits_overdue_axis(c),
            _AVAILABILITY_RANK.get(c.availability, 99),
            _TIER_RANK.get(c.source.tier, 99),
            c.source.source_id,
        )

    return tuple(sorted(candidates, key=_key))


# ---------------------------------------------------------------------------
# Convenience: plan + route + axis priority + quota in one call
# ---------------------------------------------------------------------------


def select_routed_due(
    plan: Any,
    *,
    role_id: str,
    registry: Optional[KnowledgeProviderRegistry] = None,
    env: Mapping[str, str] = (),  # type: ignore[assignment]
    overdue_axes: Sequence[str] = (),
    tick_quota: Optional[int] = None,
) -> Tuple[
    Tuple[RoutedRefreshCandidate, ...],
    Tuple[RoutedRefreshCandidate, ...],
]:
    """One-call helper: route *plan*, axis-prioritise, then truncate.

    Returns ``(selected_due, deferred_due)`` — *selected_due* are the
    candidates that fit under *tick_quota* after axis priority,
    *deferred_due* are the rest (still due, but not picked this tick).

    *tick_quota* is the cap applied *after* axis prioritisation. When
    ``None`` no cap is applied (the planner already truncated). When
    set, the operator can run ``compute_refresh_plan(..., tick_quota=-1)``
    to disable the planner's cap and re-cap here using axis-aware
    ordering. Skipped entries from the original plan are returned
    separately via :func:`route_refresh_plan`; this helper focuses on
    the "due" side only.
    """

    due, _ = route_refresh_plan(
        plan, role_id=role_id, registry=registry, env=env
    )
    ordered = axis_priority_order(due, overdue_axes=overdue_axes)
    if tick_quota is None or tick_quota < 0:
        return ordered, ()
    return ordered[:tick_quota], ordered[tick_quota:]


__all__ = [
    "RoutedRefreshCandidate",
    "axis_priority_order",
    "route_refresh_plan",
    "select_routed_due",
]
