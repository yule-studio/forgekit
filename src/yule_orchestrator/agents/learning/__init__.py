"""agents.learning — F2 hookify seam.

Issue #89 (parent #81) extends the round-1 lifecycle ledger with a
persistent SQLite-backed mistake ledger and a preflight judgement
seam that can advise / warn / block a role-specific action.

The round-1 surface under
:mod:`yule_orchestrator.agents.lifecycle.mistake_ledger` continues to
own the session-extra ladder. This module is the cross-session
counterpart — its records survive process restarts and feed the
preflight hook a caller drops in just before
``coding_executor_worker._run_pipeline`` (or any other role-take
worker).

The two modules **do not import each other** by design — a recurring
mistake recorded on the session-extra ledger is promoted to this
durable ledger by the postmortem producer (see
:func:`yule_orchestrator.agents.learning.mistake_ledger.mistake_candidate_from_postmortem`)
so the seam remains explicit.

Hard rails (the kind every later integration must respect):

  * ``BlockerLevel.BLOCK`` verdicts never auto-execute — the caller
    routes the work to the existing ``needs_approval`` lane and the
    preflight verdict records the recommendation.
  * ``MistakeLedger.resolve`` is the only path that flips a record to
    resolved — there is no auto-dismiss.
  * The ledger never grows unbounded — ``prune_old_resolved`` is the
    explicit retention hook the operator (or a scheduled job) runs.
"""

from .mistake_ledger import (
    BlockerLevel,
    MistakeLedger,
    MistakeRecord,
    mistake_candidate_from_postmortem,
)
from .preflight import (
    PreflightVerdict,
    judge_preflight,
)


__all__ = (
    "BlockerLevel",
    "MistakeLedger",
    "MistakeRecord",
    "PreflightVerdict",
    "judge_preflight",
    "mistake_candidate_from_postmortem",
)
