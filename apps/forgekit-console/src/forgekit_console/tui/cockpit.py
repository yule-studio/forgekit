"""Operator-cockpit status facts — the real numbers behind the issue-line badges.

Pure (no textual / no widget) so it is unit-testable everywhere, including CI without a
TUI installed. The app's issue line delegates here for the two control-plane facts an
operator otherwise had to poll for:

* ``awaiting`` — goals parked in ``awaiting_approval`` (the operator must decide). REUSES
  ``goal_continuity_status`` — the SAME snapshot ``forgekit runtime status`` reads — so the
  console never re-implements the count.
* ``budget_ratio`` — today's token spend ÷ the configured ``daily_token_budget``, from the
  real usage ledger. ``None`` when no budget is configured (unbounded).

Honesty rail: a goal-store / ledger read failure degrades to NO badge (0 / None), never a
fabricated number. All heavy imports are lazy so importing this module is cheap + dep-free.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple


def cockpit_badges(
    *, env: Optional[Mapping[str, str]] = None, config: Optional[Mapping] = None,
    ledger_path=None,
) -> Tuple[int, Optional[float]]:
    """Return ``(awaiting_count, budget_ratio)`` from the LIVE stores (best-effort).

    Both reads are defensive: any failure degrades to no-badge so the status line can never
    break — and never shows a guessed number."""

    awaiting = 0
    try:
        from forgekit_runtime.runtime.goal_status import goal_continuity_status

        st = goal_continuity_status(env=env or None)
        awaiting = st.awaiting_approval if st.available else 0
    except Exception:  # noqa: BLE001 - goal store read is best-effort
        awaiting = 0

    budget_ratio: Optional[float] = None
    try:
        from ..usage import budget_from_config, read_events, rollup, today

        budget = budget_from_config(config)
        if budget > 0:
            spent = rollup(read_events(path=ledger_path, day=today())).total_tokens
            budget_ratio = spent / budget
    except Exception:  # noqa: BLE001 - ledger read is best-effort
        budget_ratio = None
    return awaiting, budget_ratio


__all__ = ("cockpit_badges",)
