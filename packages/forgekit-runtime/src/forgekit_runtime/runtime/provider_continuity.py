"""Wrap a tick_fn so every tick records its provider lane + budget — continuity.

This is the seam that connects the goal/autopilot loop (which advances work) to the
provider/runtime axis (which provider lane it routed through, within what budget). It does
NOT change what a tick *does*; it observes the resolved lane + the day's budget after each
tick and:

  1. appends a durable :class:`TickRecord` (provider lane · executed · budget · receipt),
  2. enriches the :class:`TickOutcome` so the heartbeat/status surfaces the lane + budget.

So a user who sets one ``/goal`` and leaves ``runtime serve`` running gets a durable,
operator-trackable trail of how the loop kept progressing, through which provider, within
budget — tick by tick. Pure wiring over existing routing/budget/ledger (no new engine);
config/env injectable for deterministic tests.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping, Optional

from forgekit_provider.policy.provider_config import load_provider_config
from forgekit_provider.usage import ledger as U
from forgekit_provider.usage.budget import BudgetState, budget_from_config, evaluate_budget

from forgekit_provider.policy import provider_config as pc

from .daemon import TickOutcome
from .provider_lane import resolve_tick_lane
from .tick_ledger import TickRecord, append_tick_record


def budget_snapshot(config: Optional[Mapping], *,
                    env: Optional[Mapping[str, str]] = None) -> BudgetState:
    """Today's token spend vs the configured daily budget (honest 0 = unbounded)."""

    day = U.today(env)
    spent = 0
    for row in U.read_events(env=env, day=day):
        try:
            spent += int(row.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            continue
    return evaluate_budget(spent, budget_from_config(config))


def with_provider_continuity(tick_fn: Callable[[int], TickOutcome], *,
                             config: Optional[Mapping],
                             env: Optional[Mapping[str, str]] = None,
                             slot: str = "",
                             supported: Optional[Callable[[str], bool]] = None,
                             available: Optional[Callable[[str], bool]] = None,
                             append: bool = True) -> Callable[[int], TickOutcome]:
    """Return a tick_fn that records provider lane + budget each tick (durable + surfaced)."""

    cfg = load_provider_config(config)
    use_slot = slot or pc.SLOT_EXECUTION

    def wrapped(n: int) -> TickOutcome:
        out = tick_fn(n)
        lane = resolve_tick_lane(cfg, slot=use_slot, supported=supported, available=available)
        bs = budget_snapshot(config, env=env)
        rec = TickRecord(
            tick=n, ts=U.now_ts(env), lane=lane.to_dict(), executed=out.executed,
            blocked=out.blocked_count, waiting=out.waiting,
            executed_paths=tuple(out.executed_paths), budget=bs.to_dict(),
            skipped_reason=out.skipped_reason)
        if append:
            append_tick_record(rec, env=env)
        budget_tag = f"budget {bs.spent}/{bs.budget or '∞'}tok"
        summary = " | ".join(p for p in (out.summary, f"lane {lane.short()}", budget_tag) if p)
        return replace(out, summary=summary, provider_lane=lane.to_dict(), budget=bs.to_dict())

    return wrapped


__all__ = ("budget_snapshot", "with_provider_continuity")
