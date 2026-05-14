"""F16 PR-2 — Discord adapter: enqueue a PR merge approval card.

Mirrors :mod:`yule_orchestrator.discord.github_workos_adapter` (the
ENGINEERING_WRITE path) but for a different
:data:`~yule_orchestrator.agents.job_queue.pr_approval.APPROVAL_KIND_PR_MERGE`.

The adapter does **NOT** call GitHub. It receives a
:class:`PRMergeProposal` from a producer (``pr_event_producer``) and
builds the :class:`ApprovalRequest` that the
:class:`~yule_orchestrator.agents.job_queue.approval_worker.ApprovalWorker`
posts to ``#승인-대기``.

Why a separate adapter (not reuse ENGINEERING_WRITE):

  * The reply vocabulary differs — PR merge supports
    "수정 후 다시" (REVISE_AND_REPEAT).
  * The downstream action differs — ENGINEERING_WRITE produces a
    work order; PR merge invokes the 5-step gate then
    ``live_client.merge_pull_request``.
  * Audit trails diverge — PR merge has a distinct
    ``session.extra["pr_merge_audit"]`` bucket.

Both adapters still go through the same ``ApprovalWorker``, so the
queue dedup + Discord posting code stays a single implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ...agents.job_queue.approval_reply import find_replyable_approval
from ...agents.job_queue.approval_worker import ApprovalRequest, ApprovalWorker
from ...agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeProposal,
    render_pr_merge_summary,
)


# Skipped reasons surfaced by :class:`PRMergeApprovalOutcome`.
SKIPPED_DUPLICATE_PR_MERGE_CARD: str = "duplicate_pr_merge_card_in_flight"
SKIPPED_NO_PROPOSAL: str = "no_proposal"


@dataclass(frozen=True)
class PRMergeApprovalOutcome:
    """Result of :func:`enqueue_pr_merge_approval`.

    Mirrors :class:`GitHubWorkApprovalOutcome` shape so the gateway
    can treat either adapter's return uniformly.
    """

    proposal: Optional[PRMergeProposal]
    approval_job_id: Optional[str] = None
    approval_post_outcome: Optional[Any] = None
    skipped_reason: Optional[str] = None


def _approval_request_from_proposal(
    proposal: PRMergeProposal,
    *,
    session_id: str,
    source_channel_id: Optional[int],
    source_thread_id: Optional[int],
    source_message_id: Optional[int],
) -> ApprovalRequest:
    """Convert a :class:`PRMergeProposal` into an :class:`ApprovalRequest`.

    The summary card body lives in
    :attr:`ApprovalRequest.summary` so the approval worker's render
    can re-use the proposal's existing markdown without re-fetching
    GitHub state.
    """

    title = (
        f"PR 머지 승인 — #{proposal.pr_number}"
        + (f" {proposal.pr_title}" if proposal.pr_title else "")
    )
    requested_action = "PR merge approval — 5-step gate 통과 시 merge"

    return ApprovalRequest(
        session_id=session_id,
        approval_kind=APPROVAL_KIND_PR_MERGE,
        title=title,
        summary=render_pr_merge_summary(proposal),
        requested_action=requested_action,
        created_by=proposal.requested_by or "",
        source_channel_id=source_channel_id,
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
        extra=dict(proposal.to_extra()),
    )


async def enqueue_pr_merge_approval(
    *,
    session: Any,
    proposal: Optional[PRMergeProposal],
    approval_worker: ApprovalWorker,
    source_channel_id: Optional[int] = None,
    source_thread_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    drive_consumer: bool = True,
    now: Optional[float] = None,
) -> PRMergeApprovalOutcome:
    """Build the approval card and post it to ``#승인-대기``.

    Returns :class:`PRMergeApprovalOutcome` describing what happened.
    Two side-effects:

      1. Insert a row into the approval_post queue (idempotent —
         dedup keyed on ``(session, kind, source_message_id, head_sha)``).
      2. (Only when *drive_consumer* is True, the production default)
         immediately drive ``ApprovalWorker.run_one`` so the card
         posts to ``#승인-대기`` in the same call. Tests that want
         to inspect the queue without posting set this to False.

    The function never calls GitHub.
    """

    if proposal is None:
        return PRMergeApprovalOutcome(
            proposal=None,
            skipped_reason=SKIPPED_NO_PROPOSAL,
        )

    session_id = _resolve_session_id(session)
    if not session_id:
        return PRMergeApprovalOutcome(
            proposal=proposal,
            skipped_reason="session_id_missing",
        )

    # Dedup: same session + same head_sha + (optionally) same source
    # message must not produce a second card. We extend the standard
    # ``find_replyable_approval`` filter with a head_sha equality check
    # because the same PR may legitimately get a new card after a
    # commit (sha changes); we want to skip only when the *same* sha
    # already has a SAVED card.
    queue = approval_worker._queue  # noqa: SLF001 - intentional reuse
    existing = find_replyable_approval(
        queue=queue,
        session_id=session_id,
        approval_kind=APPROVAL_KIND_PR_MERGE,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    if existing is not None:
        prev_sha = (existing.payload or {}).get("extra", {}).get("head_sha", "")
        if prev_sha and prev_sha == proposal.head_sha:
            return PRMergeApprovalOutcome(
                proposal=proposal,
                approval_job_id=existing.job_id,
                approval_post_outcome=None,
                skipped_reason=SKIPPED_DUPLICATE_PR_MERGE_CARD,
            )

    request = _approval_request_from_proposal(
        proposal,
        session_id=session_id,
        source_channel_id=source_channel_id,
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
    )

    if drive_consumer:
        outcome = await approval_worker.run_one(request, now=now)
        approval_job_id = (
            outcome.job.job_id if getattr(outcome, "job", None) else None
        )
        skipped = None
        if getattr(outcome, "skipped_reason", None) == "duplicate_in_flight":
            skipped = SKIPPED_DUPLICATE_PR_MERGE_CARD
        elif getattr(outcome, "skipped_reason", None):
            skipped = outcome.skipped_reason
        return PRMergeApprovalOutcome(
            proposal=proposal,
            approval_job_id=approval_job_id,
            approval_post_outcome=outcome,
            skipped_reason=skipped,
        )

    job, created = approval_worker.enqueue(request, now=now)
    return PRMergeApprovalOutcome(
        proposal=proposal,
        approval_job_id=job.job_id if job is not None else None,
        approval_post_outcome=None,
        skipped_reason=None if created else SKIPPED_DUPLICATE_PR_MERGE_CARD,
    )


def _resolve_session_id(session: Any) -> str:
    """Be tolerant of dict / dataclass / SimpleNamespace session shapes."""

    if session is None:
        return ""
    candidate = (
        getattr(session, "session_id", None)
        or (session.get("session_id") if isinstance(session, Mapping) else None)
    )
    return str(candidate or "")


__all__ = (
    "PRMergeApprovalOutcome",
    "SKIPPED_DUPLICATE_PR_MERGE_CARD",
    "SKIPPED_NO_PROPOSAL",
    "enqueue_pr_merge_approval",
)
