"""Completion hook + standardised end-state — Phase 2 of #73.

Every queue worker (research / role / approval / obsidian / coding-executor)
should funnel its terminal transition through this module. The hook:

  1. Maps the worker-side outcome to one of 4 standard states
     (`done` / `blocked` / `needs_approval` / `retry_ready`).
  2. Stamps an :class:`AgentOpsEntry` so the audit trail tracks why
     the job ended where it did (and whether next-task selection is
     advisable).
  3. Returns a :class:`CompletionRouting` hint the next-task selector
     consumes (`should_select_next`, `recommended_source`, etc.).

The hook itself never enqueues the next job — that's the caller's
responsibility (typically a small wrapper in the worker's run-loop).
This keeps the hook side-effect-light and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from ..lifecycle.agent_ops_log import (
    AgentOpsEntry,
    append_agent_ops_audit,
    build_agent_ops_entry,
)
from ..lifecycle.autonomy_policy import (
    ACTION_FAILURE_AUDIT_RECORD,
    ACTION_FORUM_HANDOFF_DECISION,
    AutonomyContext,
    decide_autonomy,
)


# ---------------------------------------------------------------------------
# Standardised end-state vocabulary
# ---------------------------------------------------------------------------


COMPLETION_DONE: str = "done"
COMPLETION_BLOCKED: str = "blocked"
COMPLETION_NEEDS_APPROVAL: str = "needs_approval"
COMPLETION_RETRY_READY: str = "retry_ready"


COMPLETION_STATUSES: tuple = (
    COMPLETION_DONE,
    COMPLETION_BLOCKED,
    COMPLETION_NEEDS_APPROVAL,
    COMPLETION_RETRY_READY,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobCompletionEvent:
    """One worker job's terminal outcome.

    Producers build this just before calling
    :func:`record_completion`. ``reason`` is human-readable; the
    structured ``metadata`` carries machine-parseable fields the
    next-task selector + audit log read.
    """

    job_id: str
    job_type: str
    session_id: str
    status: str
    reason: str = ""
    role: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    completed_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "session_id": self.session_id,
            "status": self.status,
            "reason": self.reason,
            "role": self.role,
            "metadata": dict(self.metadata),
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class CompletionRouting:
    """Hint passed back to the worker run-loop.

    ``should_select_next`` is True when the next-task selector should
    fire on this completion. ``blocking_reason`` is non-empty when the
    selector should NOT fire (e.g. job blocked on external secret).
    """

    status: str
    should_select_next: bool
    recommended_source: Optional[str] = None
    blocking_reason: Optional[str] = None
    audit_entry_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalise_completion_status(value: str) -> str:
    """Coerce a worker-side string to one of the 4 standard statuses.

    Liberal in what it accepts:

      * ``"saved"`` / ``"completed"`` / ``"ok"`` → ``done``
      * ``"failed_retryable"`` / ``"retry"`` / ``"transient"`` → ``retry_ready``
      * ``"pending_approval"`` / ``"needs_approval"`` → ``needs_approval``
      * ``"blocked"`` / ``"failed_terminal"`` / ``"manual"`` → ``blocked``

    Anything unrecognised falls back to ``blocked`` (safest default —
    selector won't auto-pick the next task without a clear signal).
    """

    raw = (value or "").strip().lower()
    if raw in {"done", "saved", "completed", "ok", "success"}:
        return COMPLETION_DONE
    if raw in {"retry_ready", "failed_retryable", "retry", "transient"}:
        return COMPLETION_RETRY_READY
    if raw in {"needs_approval", "pending_approval", "approval_required"}:
        return COMPLETION_NEEDS_APPROVAL
    return COMPLETION_BLOCKED


def record_completion(
    *,
    event: JobCompletionEvent,
    session_extra: Optional[Mapping[str, Any]] = None,
) -> tuple[Mapping[str, Any], CompletionRouting]:
    """Stamp the completion to the audit log + return routing hint.

    *session_extra* is the existing ``session.extra`` dict (read-only
    input). The returned mapping is a *new* extra dict the caller
    persists via the same path the existing forum-handoff audit uses
    (``workflow_state.update_session``).

    Side effects: only the returned dict + an in-memory
    :class:`AgentOpsEntry`. The function does no I/O.
    """

    status = normalise_completion_status(event.status)
    routing = _build_routing(status, event)

    decision = decide_autonomy(
        AutonomyContext(
            action=_action_for_status(status),
            session_id=event.session_id,
            job_id=event.job_id,
            summary=_summary_for_event(event, status),
            reason=event.reason or _default_reason_for(status),
        )
    )
    audit = build_agent_ops_entry(
        decision=decision,
        outcome=f"job_completion:{status}:{event.job_type}",
        summary=_summary_for_event(event, status),
        job_id=event.job_id,
    )
    new_extra = append_agent_ops_audit(session_extra or {}, audit)
    routing = CompletionRouting(
        status=routing.status,
        should_select_next=routing.should_select_next,
        recommended_source=routing.recommended_source,
        blocking_reason=routing.blocking_reason,
        audit_entry_id=audit.entry_id,
    )
    return new_extra, routing


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_routing(status: str, event: JobCompletionEvent) -> CompletionRouting:
    if status == COMPLETION_DONE:
        # Selector fires — the standard happy path.
        return CompletionRouting(
            status=status,
            should_select_next=True,
            recommended_source=_source_for_done(event.job_type),
        )
    if status == COMPLETION_RETRY_READY:
        # Selector fires but recommends the same source (retry the
        # same job type after backoff).
        return CompletionRouting(
            status=status,
            should_select_next=True,
            recommended_source="retry_same",
        )
    if status == COMPLETION_NEEDS_APPROVAL:
        # Selector defers — the human approval path is owned by
        # the approval_post worker, not the selector.
        return CompletionRouting(
            status=status,
            should_select_next=False,
            blocking_reason="awaiting_human_approval",
        )
    # COMPLETION_BLOCKED — selector deferred; operator notification
    # surface (gateway-mediated comment / #봇-상태) takes over.
    return CompletionRouting(
        status=status,
        should_select_next=False,
        blocking_reason=event.reason or "blocked_external",
    )


def _source_for_done(job_type: str) -> str:
    """Heuristic: which selector source the *next* hop should look at."""

    mapping = {
        "research_collect": "deliberation_after_research",
        "role_take": "synthesis_after_takes",
        "approval_post": "obsidian_or_coding_after_approval",
        "obsidian_write": "next_task_default",
        "coding_execute": "next_task_default",
    }
    return mapping.get(job_type, "next_task_default")


def _action_for_status(status: str) -> str:
    if status in (COMPLETION_DONE, COMPLETION_RETRY_READY):
        return ACTION_FORUM_HANDOFF_DECISION
    return ACTION_FAILURE_AUDIT_RECORD


def _default_reason_for(status: str) -> str:
    if status == COMPLETION_DONE:
        return "L1 — job completed; next-task selector eligible"
    if status == COMPLETION_RETRY_READY:
        return "L1 — transient failure; same source retry eligible"
    if status == COMPLETION_NEEDS_APPROVAL:
        return "L1 — job awaiting human approval; selector deferred"
    return "L1 — job blocked; operator surface notified"


def _summary_for_event(event: JobCompletionEvent, status: str) -> str:
    base = f"{event.job_type} 완료 (status={status})"
    if event.reason:
        return f"{base} — {event.reason}"
    return base


__all__ = (
    "COMPLETION_BLOCKED",
    "COMPLETION_DONE",
    "COMPLETION_NEEDS_APPROVAL",
    "COMPLETION_RETRY_READY",
    "COMPLETION_STATUSES",
    "CompletionRouting",
    "JobCompletionEvent",
    "normalise_completion_status",
    "record_completion",
)
