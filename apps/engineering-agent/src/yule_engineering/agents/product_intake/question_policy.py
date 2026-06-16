"""PM question policy — ask few, ask precisely.

Rules:
  * **budget ≤ 3** decision questions. The rest is auto-filled as assumptions /
    recommended defaults, never asked.
  * **no open-ended questions** — every question carries options and exactly one
    recommended option.
  * **priority**: irreversible / billing / permission / visibility / publish /
    ordering / external-integration first (see ``CATEGORY_PRIORITY``). Safe,
    reversible defaults are assumed, not asked.
"""

from __future__ import annotations

from typing import Sequence, Tuple

from .families import TEMPLATE_BY_KEY
from .models import CATEGORY_PRIORITY, DecisionQuestion

MAX_QUESTIONS = 3


def is_well_formed(question: DecisionQuestion) -> bool:
    """A question must be option-shaped with exactly one recommended option."""

    recommended = [o for o in question.options if o.recommended]
    return len(question.options) >= 2 and len(recommended) == 1


def _priority(question: DecisionQuestion) -> int:
    try:
        return CATEGORY_PRIORITY.index(question.category)
    except ValueError:
        return len(CATEGORY_PRIORITY)


def select_questions(
    decision_keys: Sequence[str], *, budget: int = MAX_QUESTIONS
) -> Tuple[DecisionQuestion, ...]:
    """Map decision keys → templates, keep only well-formed, prioritise, cap.

    De-dupes by id, drops anything not option-shaped, sorts by category priority
    (stable within a category), and truncates to *budget* — so we always ask the
    most consequential, reversible-last, and never more than the budget.
    """

    seen = set()
    chosen: list[DecisionQuestion] = []
    for key in decision_keys:
        q = TEMPLATE_BY_KEY.get(key)
        if q is None or q.id in seen or not is_well_formed(q):
            continue
        seen.add(q.id)
        chosen.append(q)
    chosen.sort(key=_priority)
    return tuple(chosen[: max(0, budget)])


def deferred_keys(decision_keys: Sequence[str], asked: Sequence[DecisionQuestion]) -> Tuple[str, ...]:
    """Decision keys that were dropped by the budget — these become assumptions."""

    asked_ids = {q.id for q in asked}
    out = []
    for key in decision_keys:
        if key in TEMPLATE_BY_KEY and key not in asked_ids and key not in out:
            out.append(key)
    return tuple(out)


__all__ = ("MAX_QUESTIONS", "is_well_formed", "select_questions", "deferred_keys")
