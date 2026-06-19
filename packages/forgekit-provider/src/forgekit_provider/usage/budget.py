"""Budget threshold / reserve / throttle (WT2) — operator-actionable alerts.

A daily token budget (from config) + the today rollup → which thresholds (70/85/100%)
were crossed. Crossing surfaces to ≥2 places (operator inbox + console alert) with an
action-oriented message. The same budget feeds the WT1 submit gate's throttle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

DEFAULT_THRESHOLDS = (0.70, 0.85, 1.00)


def budget_from_config(config: Optional[Mapping] = None) -> int:
    """Daily token budget from config (`daily_token_budget`). 0 = unbounded."""

    try:
        return max(0, int((config or {}).get("daily_token_budget", 0) or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class BudgetState:
    spent: int
    budget: int
    crossed: Tuple[float, ...] = ()   # thresholds crossed (e.g. (0.7, 0.85))

    @property
    def ratio(self) -> float:
        return (self.spent / self.budget) if self.budget > 0 else 0.0

    @property
    def over(self) -> bool:
        return self.budget > 0 and self.spent >= self.budget

    @property
    def highest_crossed(self) -> float:
        return max(self.crossed) if self.crossed else 0.0

    def to_dict(self) -> dict:
        return {"spent": self.spent, "budget": self.budget, "ratio": round(self.ratio, 3),
                "crossed": list(self.crossed), "over": self.over}


def evaluate_budget(spent: int, budget: int,
                    thresholds: Tuple[float, ...] = DEFAULT_THRESHOLDS) -> BudgetState:
    """Which thresholds did *spent* cross against *budget*? (budget 0 → none)."""

    if budget <= 0:
        return BudgetState(spent, budget, ())
    ratio = spent / budget
    crossed = tuple(t for t in sorted(thresholds) if ratio >= t)
    return BudgetState(spent, budget, crossed)


def alert_message(state: BudgetState) -> str:
    """An operator-actionable budget message (무엇/왜/지금 뭘)."""

    pct = int(state.ratio * 100)
    if state.over:
        return (f"오늘 budget 초과: {state.spent}/{state.budget}tok ({pct}%). "
                "cost-save 모드로 전환하거나 budget 을 상향하세요.")
    return (f"오늘 budget {int(state.highest_crossed*100)}% 도달: {state.spent}/{state.budget}tok. "
            "비싼 작업을 미루거나 cheap provider/cost-save 모드를 고려하세요.")


__all__ = (
    "DEFAULT_THRESHOLDS", "budget_from_config", "BudgetState",
    "evaluate_budget", "alert_message",
)
