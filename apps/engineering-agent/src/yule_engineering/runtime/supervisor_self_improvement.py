"""Self-improvement runtime loop wiring (P0-SI) — supervisor branch helper.

Bridges :class:`SelfImprovementDispatcher` into the supervisor watch loop's
``self_improvement_detect_fn`` + ``self_improvement_dispatch_fn`` hook pair.
Extracted from :mod:`run_service` so that module stays under the split-now LOC
ceiling and the *self-improvement* responsibility lives on its own seam.

Disabled by default — the operator opts in via ``YULE_SELF_IMPROVEMENT_ENABLED=1``
so an unconfigured production host behaves exactly as before this landed.

Hard rails:
  * No auto-merge / push to protected branches / deploy / secret modify ever
    runs through this path — the delegated_operator policy's permanent
    escalation list blocks those at the boundary, and the executor handoff hook
    stamps ``draft_pr_only=True`` so a downstream coding executor that *did*
    support auto-merge could not.
  * Failures are logged + swallowed so this never crashes the supervisor.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


def build_self_improvement_loop(
    *, queue: Any, heartbeats: Any
) -> Tuple[Optional[Callable], Optional[Callable], Optional[float]]:
    """Return ``(detect_fn, dispatch_fn, interval_seconds)`` for the
    supervisor watch loop.

    Returns ``(None, None, None)`` when the operator hasn't opted in via
    ``YULE_SELF_IMPROVEMENT_ENABLED`` — that's the default and keeps the
    supervisor identical to before for installations that don't want
    autonomous problem handling.
    """

    try:
        from ..agents.lifecycle.runtime_self_improvement_wiring import (
            ENV_SUPERVISOR_SESSION_ID,
            build_queue_executor_enqueue_fn,
            build_self_improvement_dispatcher,
            enqueue_enabled,
            is_enabled,
            resolve_interval_seconds,
        )
    except Exception:  # noqa: BLE001 - import failure must not kill supervisor
        logger.warning(
            "self-improvement runtime wiring import failed", exc_info=True
        )
        return None, None, None

    if not is_enabled():
        return None, None, None

    interval = resolve_interval_seconds()

    # Default: journal + ledger only. When the operator also opts into
    # ENV_ENQUEUE_ENABLED, detected proposals become real coding_execute jobs
    # (draft-PR only) on the queue — closing the proposal → queue surface.
    executor_enqueue_fn = None
    if enqueue_enabled():
        try:
            executor_enqueue_fn = build_queue_executor_enqueue_fn(job_queue=queue)
        except Exception:  # noqa: BLE001 - never crash supervisor on wiring
            logger.warning(
                "self-improvement queue handoff construction failed; "
                "loop will journal without queue dispatch",
                exc_info=True,
            )
            executor_enqueue_fn = None
    supervisor_session_id = (os.environ.get(ENV_SUPERVISOR_SESSION_ID) or "").strip() or None

    try:
        dispatcher = build_self_improvement_dispatcher(
            job_queue=queue,
            heartbeat_store=heartbeats,
            executor_enqueue_fn=executor_enqueue_fn,
            obsidian_supervisor_session_id=supervisor_session_id,
        )
    except Exception:  # noqa: BLE001 - never crash supervisor on wiring
        logger.warning(
            "self-improvement dispatcher construction failed; "
            "supervisor will tick without self-improvement loop",
            exc_info=True,
        )
        return None, None, None

    logger.info(
        "self-improvement runtime loop enabled "
        "(interval=%.1fs, ledger=%s, queue_handoff=%s)",
        interval,
        dispatcher.problem_ledger._path,  # type: ignore[attr-defined]
        "on" if executor_enqueue_fn is not None else "off",
    )

    # Wrap dispatch_fn so each per-signal dispatch also updates the
    # process-local journal — operator status post + journalctl both read from
    # there. Failures are logged + swallowed so a journaling bug never crashes
    # the supervisor.
    from ..agents.lifecycle.runtime_self_improvement_loop import (
        SelfImprovementTickReport,
    )
    from .self_improvement_status import record_tick

    raw_dispatch_fn = dispatcher.dispatch_fn

    def _wrapped_dispatch(signal, plan):
        outcome = raw_dispatch_fn(signal, plan)
        try:
            # Build a single-signal pseudo-report so the journal entry still
            # surfaces in the absence of a paired run_tick call.
            problem = outcome.problem
            single_report = SelfImprovementTickReport(
                detected_signals=(signal,),
                new_problems=(problem,) if problem.occurrence_count == 1 else (),
                handled=(outcome,),
                skipped_terminal=(),
                delegated_count=1 if outcome.problem.delegated_ok else 0,
                waiting_operator_count=(
                    1 if outcome.final_status.value == "waiting_operator" else 0
                ),
                blocked_count=(1 if outcome.final_status.value == "blocked" else 0),
            )
            record_tick(single_report)
        except Exception:  # noqa: BLE001 - journaling must not break dispatch
            logger.warning(
                "self-improvement status journal record raised", exc_info=True
            )
        return outcome

    return dispatcher.detect_fn, _wrapped_dispatch, interval


__all__ = ("build_self_improvement_loop",)
