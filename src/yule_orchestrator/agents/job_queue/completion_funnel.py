"""Completion funnel — Round 4 of #73.

The :mod:`completion_hook` already standardises every worker's
terminal outcome into one of 4 states (``done`` / ``blocked`` /
``needs_approval`` / ``retry_ready``) and stamps an audit entry.

What was missing was the *next-step trigger*: a single funnel that
takes the routing hint produced by :func:`record_completion` and
either fires the autonomy producer (so the runtime keeps moving) or
deliberately stops (when a human approval / external block needs to
intervene).

The funnel is deliberately thin — it never queues directly; it only
asks the autonomy producer to run a tick. That keeps the producer's
sub-producers as the sole source of "what to enqueue next" logic.

Funnel rules (mirror ``CompletionRouting.should_select_next``):

  * ``done`` → trigger producer tick. Recommended source carried
    forward for telemetry only.
  * ``retry_ready`` → trigger producer tick. The CI retry orchestrator
    has already requeued the row; this tick lets the selector re-rank
    the world view (e.g. promote the next coding_job).
  * ``needs_approval`` → do NOT trigger. The approval worker owns the
    next hop.
  * ``blocked`` → do NOT trigger. Operator surface notified via the
    audit row + supervisor status post.

The funnel returns a small audit record so callers can log a single
structured line per completion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

from .completion_hook import (
    COMPLETION_BLOCKED,
    COMPLETION_DONE,
    COMPLETION_NEEDS_APPROVAL,
    COMPLETION_RETRY_READY,
    CompletionRouting,
    JobCompletionEvent,
    record_completion,
)


logger = logging.getLogger(__name__)


__all__ = (
    "COMPLETION_FUNNEL_EXTRA_KEY",
    "CompletionFunnelOutcome",
    "FunnelDecision",
    "build_completion_funnel",
    "funnel_completion",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Top-level ``session.extra`` slot the funnel writes its decision to.
# Producers / status posters read this when they want to surface
# "last completion → producer tick fired" telemetry.
COMPLETION_FUNNEL_EXTRA_KEY: str = "completion_funnel"


# ---------------------------------------------------------------------------
# Outcome models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunnelDecision:
    """Why the funnel did or did not trigger the producer.

    ``ticked`` is True when the funnel called the producer's
    ``tick()``. ``producer_summary`` is the producer report's
    summary line, captured here so the operator surface can join
    completion + scheduler view in one structured log entry.
    """

    completion_status: str
    ticked: bool
    reason: str
    recommended_source: Optional[str] = None
    audit_entry_id: Optional[str] = None
    producer_summary: Optional[str] = None


@dataclass(frozen=True)
class CompletionFunnelOutcome:
    """Full result of one funnel call.

    Bundles the new ``session.extra`` produced by
    :func:`record_completion` with the funnel's decision so callers
    can persist + log atomically.
    """

    new_session_extra: Mapping[str, Any]
    routing: CompletionRouting
    decision: FunnelDecision


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def funnel_completion(
    *,
    event: JobCompletionEvent,
    session_extra: Optional[Mapping[str, Any]] = None,
    producer_tick_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> CompletionFunnelOutcome:
    """Apply the standard hook then decide whether to advance the queue.

    *producer_tick_fn* is an injectable callable — production wires it
    to ``AutonomyProducer.tick``; tests pass a stub that records calls.
    When ``None`` the funnel still runs ``record_completion`` and
    returns a decision with ``ticked=False / reason=no_producer_wired``.

    The funnel does NOT enqueue jobs directly. The producer's tick is
    the only side-effecting hop here.
    """

    new_extra, routing = record_completion(
        event=event, session_extra=session_extra
    )

    triggered = False
    summary: Optional[str] = None
    if not routing.should_select_next:
        reason = (
            routing.blocking_reason
            or f"selector deferred (status={routing.status})"
        )
    elif producer_tick_fn is None:
        reason = "no producer_tick_fn wired"
    else:
        try:
            report = producer_tick_fn()
            triggered = True
            if report is not None and hasattr(report, "summary_line"):
                try:
                    summary = report.summary_line()  # type: ignore[no-untyped-call]
                except Exception:  # noqa: BLE001
                    summary = None
            reason = f"producer ticked ({routing.status})"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "completion funnel: producer tick raised", exc_info=True
            )
            reason = f"producer tick raised: {type(exc).__name__}"

    decision = FunnelDecision(
        completion_status=routing.status,
        ticked=triggered,
        reason=reason,
        recommended_source=routing.recommended_source,
        audit_entry_id=routing.audit_entry_id,
        producer_summary=summary,
    )

    when_iso = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()
    new_extra = _stamp_funnel_decision(
        new_extra,
        decision=decision,
        event=event,
        when_iso=when_iso,
    )
    return CompletionFunnelOutcome(
        new_session_extra=new_extra,
        routing=routing,
        decision=decision,
    )


def build_completion_funnel(
    *,
    producer_tick_fn: Callable[..., Any],
) -> Callable[..., CompletionFunnelOutcome]:
    """Factory that binds the producer once and returns the funnel.

    Use this in the run-service wiring so every worker's completion
    callback funnels through the same producer instance.
    """

    def _funnel(
        *,
        event: JobCompletionEvent,
        session_extra: Optional[Mapping[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> CompletionFunnelOutcome:
        return funnel_completion(
            event=event,
            session_extra=session_extra,
            producer_tick_fn=producer_tick_fn,
            now=now,
        )

    return _funnel


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stamp_funnel_decision(
    extra: Mapping[str, Any],
    *,
    decision: FunnelDecision,
    event: JobCompletionEvent,
    when_iso: str,
) -> Mapping[str, Any]:
    """Append a structured funnel-decision row to ``session.extra``.

    Bounded to the most recent 32 entries — long-running sessions can
    accumulate many completions and we'd rather drop the oldest than
    grow the cache row indefinitely.
    """

    base = dict(extra or {})
    block = base.get(COMPLETION_FUNNEL_EXTRA_KEY)
    if not isinstance(block, Mapping):
        block = {}
    history_raw = block.get("history") if isinstance(block, Mapping) else ()
    history: List[Mapping[str, Any]] = (
        list(history_raw) if isinstance(history_raw, (list, tuple)) else []
    )
    history.append(
        {
            "job_id": event.job_id,
            "job_type": event.job_type,
            "session_id": event.session_id,
            "completion_status": decision.completion_status,
            "ticked": decision.ticked,
            "reason": decision.reason,
            "recommended_source": decision.recommended_source,
            "producer_summary": decision.producer_summary,
            "audit_entry_id": decision.audit_entry_id,
            "at": when_iso,
        }
    )
    if len(history) > 32:
        history = history[-32:]
    base[COMPLETION_FUNNEL_EXTRA_KEY] = {
        "last_completion_status": decision.completion_status,
        "last_ticked": decision.ticked,
        "last_reason": decision.reason,
        "last_at": when_iso,
        "history": history,
    }
    return base
