"""Small token-budget tracker for LLM calls.

A :class:`TokenBudget` is a *minimal* accumulator: it knows a total ceiling,
how many tokens have been spent, and how many remain. It records
:class:`yule_llm_gateway.models.TokenUsage` instances as calls complete and can
report whether a prospective spend would exceed the ceiling.

This is intentionally not a scheduler, rate limiter, or cost model — just the
arithmetic seam an agent loop COULD adopt to stop runaway token consumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .models import TokenUsage


class BudgetExceededError(RuntimeError):
    """Raised by :meth:`TokenBudget.charge` when a spend would exceed the total."""


@dataclass
class TokenBudget:
    """Track spent vs. remaining tokens against a fixed ``total`` ceiling.

    ``total`` of 0 (the default) means *unlimited* — ``remaining`` reports a
    sentinel and ``would_exceed`` is always False. This keeps the tracker usable
    as a pure observability counter when no ceiling is configured.
    """

    total: int = 0
    spent: int = 0
    history: List[TokenUsage] = field(default_factory=list)

    @property
    def unlimited(self) -> bool:
        return self.total <= 0

    @property
    def remaining(self) -> int:
        """Tokens left before the ceiling. ``-1`` when unlimited."""

        if self.unlimited:
            return -1
        return self.total - self.spent

    def would_exceed(self, usage: TokenUsage) -> bool:
        """Return True if recording *usage* would push ``spent`` past ``total``."""

        if self.unlimited:
            return False
        return self.spent + usage.total > self.total

    def record(self, usage: TokenUsage) -> "TokenBudget":
        """Record a completed call's *usage* (no ceiling enforcement).

        Use this for observability when you want to track spend but never block.
        Returns ``self`` for chaining.
        """

        self.spent += usage.total
        self.history.append(usage)
        return self

    def charge(self, usage: TokenUsage) -> "TokenBudget":
        """Record *usage*, raising :class:`BudgetExceededError` if over budget.

        Use this on the enforcing path: the usage is *not* recorded when it would
        exceed the ceiling, so the budget stays consistent after a rejection.
        """

        if self.would_exceed(usage):
            raise BudgetExceededError(
                f"token budget exceeded: spent={self.spent} "
                f"+ requested={usage.total} > total={self.total}"
            )
        return self.record(usage)

    def to_dict(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "spent": self.spent,
            "remaining": self.remaining,
        }


__all__ = (
    "TokenBudget",
    "BudgetExceededError",
)
