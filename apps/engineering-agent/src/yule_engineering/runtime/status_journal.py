"""Runtime status — autonomy journal (process-local ring buffer).

Extracted from :mod:`runtime.status` (split axis ``journal``). Owns the
in-memory ring buffer the supervisor populates after every autonomy
producer tick, plus the projection helper and module-level default
journal singleton the status builder reads back when rendering.

The journal does NOT persist — supervisor restarts naturally drop the
in-memory ticks. The agent_ops audit log on ``session.extra`` is the
durable record; this layer only answers "what is the runtime doing
right now?".
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, List, Optional, Tuple

from .status import AutonomyDispatchSummary, AutonomyTickSummary


# Default ring depth — small on purpose: the operator surface only
# needs "what happened in the last few ticks", not a full audit log
# (the agent_ops audit log + SQLite job_queue rows are the system of
# record). 16 entries comfortably covers a few minutes of producer
# activity at the default 30 s tick interval.
_AUTONOMY_JOURNAL_MAX_ENTRIES: int = 16


class RuntimeAutonomyJournal:
    """In-memory ring buffer of recent autonomy producer ticks.

    Process-local, thread-safe (the supervisor's autonomy hook may
    fire from a different task than the status post hook). Designed
    so callers can record full :class:`AutonomyProducerReport`-like
    objects and the journal projects each into a status-friendly
    :class:`AutonomyTickSummary`.

    The journal does NOT persist — supervisor restarts naturally
    drop the in-memory ticks. The agent_ops audit log on
    ``session.extra`` is the durable record; this layer only
    answers "what is the runtime doing right now?".
    """

    def __init__(self, *, max_entries: int = _AUTONOMY_JOURNAL_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._max = max(1, int(max_entries))
        self._entries: Deque[AutonomyTickSummary] = deque(maxlen=self._max)
        self._locks_held: Tuple[str, ...] = ()

    @property
    def max_entries(self) -> int:
        return self._max

    def record_report(self, report: Any) -> Optional[AutonomyTickSummary]:
        """Project *report* into a summary and append to the journal.

        Returns the projection (or ``None`` if *report* couldn't be
        projected — e.g. a stub object missing the expected fields).
        Never raises: the caller is the supervisor's last-resort hook
        and a bad projection must not derail the loop.
        """

        try:
            summary = _project_autonomy_tick(report)
        except Exception:  # noqa: BLE001 - never crash supervisor on a bad shape
            return None
        if summary is None:
            return None
        with self._lock:
            self._entries.append(summary)
            self._locks_held = tuple(summary.locks_held)
        return summary

    def recent(self, *, limit: Optional[int] = None) -> Tuple[AutonomyTickSummary, ...]:
        """Return up to *limit* ticks, newest first."""

        with self._lock:
            entries = list(self._entries)
        entries.reverse()
        if limit is not None and limit >= 0:
            entries = entries[: int(limit)]
        return tuple(entries)

    def locks_held(self) -> Tuple[str, ...]:
        """Snapshot of locks held at the most recent tick."""

        with self._lock:
            return tuple(self._locks_held)

    def reset(self) -> None:
        """Drop every recorded tick. Used by tests."""

        with self._lock:
            self._entries.clear()
            self._locks_held = ()


def _project_autonomy_tick(report: Any) -> Optional[AutonomyTickSummary]:
    """Liberal projection from any "report-like" object → summary.

    Accepts both the real :class:`AutonomyProducerReport` and any
    duck-typed object exposing the same field names (used by tests).
    """

    if report is None:
        return None
    tick_id = str(getattr(report, "tick_id", "") or "")
    started = str(getattr(report, "started_at", "") or "")
    finished = str(getattr(report, "finished_at", "") or "")
    candidate = getattr(report, "next_task_candidate", None)
    next_source: Optional[str] = None
    if candidate is not None:
        raw = getattr(candidate, "source", None)
        if raw:
            next_source = str(raw)
    summary_fn = getattr(report, "summary_line", None)
    if callable(summary_fn):
        try:
            summary_line = str(summary_fn() or "")
        except Exception:  # noqa: BLE001
            summary_line = ""
    else:
        summary_line = ""
    dispatches_raw = getattr(report, "dispatches", ()) or ()
    dispatches: List[AutonomyDispatchSummary] = []
    for entry in dispatches_raw:
        try:
            dispatches.append(
                AutonomyDispatchSummary(
                    source=str(getattr(entry, "source", "") or ""),
                    outcome=str(getattr(entry, "outcome", "") or ""),
                    session_id=str(getattr(entry, "session_id", "") or ""),
                    executor_role=str(getattr(entry, "executor_role", "") or ""),
                    job_id=getattr(entry, "job_id", None),
                    branch_hint=str(getattr(entry, "branch_hint", "") or ""),
                    reason=str(getattr(entry, "reason", "") or ""),
                )
            )
        except Exception:  # noqa: BLE001 - skip malformed dispatches
            continue
    locks_held_raw = getattr(report, "locks_held", ()) or ()
    locks_held = tuple(str(s) for s in locks_held_raw)
    error = getattr(report, "error", None)
    return AutonomyTickSummary(
        tick_id=tick_id,
        started_at=started,
        finished_at=finished,
        next_task_source=next_source,
        summary_line=summary_line,
        dispatches=tuple(dispatches),
        locks_held=locks_held,
        error=str(error) if error else None,
    )


# Module-level default journal — the supervisor's autonomy hook
# writes to it, and :func:`build_runtime_status` reads from it. Tests
# can pass an explicit instance to keep state isolated.
_DEFAULT_JOURNAL: RuntimeAutonomyJournal = RuntimeAutonomyJournal()


def get_default_autonomy_journal() -> RuntimeAutonomyJournal:
    """Return the process-local default journal singleton."""

    return _DEFAULT_JOURNAL


def record_autonomy_report(report: Any) -> Optional[AutonomyTickSummary]:
    """Record *report* into the default journal — supervisor hook.

    Returns the projected summary (or ``None`` if the projection
    failed). Never raises — calling this from the supervisor's
    on-report callback must be safe regardless of report shape.
    """

    return _DEFAULT_JOURNAL.record_report(report)
