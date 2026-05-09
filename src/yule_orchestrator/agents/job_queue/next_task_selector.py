"""Next-task selector — Phase 2 of #73.

Pure-Python selector that decides what the tech-lead runtime should
do next, given the current state of:

  * GitHub-side world: open PRs (with CI status), open issues without
    a session attached.
  * Yule-side world: approved coding_jobs ready to execute, unresolved
    discussion threads.

The selector is *deterministic* — same inputs always pick the same
candidate. Order of priorities:

  1. CI failed active PR → re-plan / retry.
  2. Approved coding_job=ready → coding_execute job.
  3. Unresolved discussion thread → role_take / research_collect refresh.
  4. Orphan open issue (no session attached) → intake.

When nothing matches, returns ``None`` and the run-loop idles.

The selector does *not* enqueue — it only returns a candidate. The
caller decides whether to enqueue (typically yes, but tests / CLI
diagnostics may want a dry preview).

External lookups are :class:`Protocol` injection seams (``GithubStateLike``,
``SessionStateLike``) so unit tests pass fakes and production wires
through G6 LiveGithubAppClient + workflow_state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# Source identifiers — what kind of next task we picked.
# ---------------------------------------------------------------------------


SOURCE_CI_FAILED_PR: str = "ci_failed_pr"
SOURCE_APPROVED_CODING_JOB: str = "approved_coding_job"
SOURCE_UNRESOLVED_DISCUSSION: str = "unresolved_discussion"
SOURCE_ORPHAN_OPEN_ISSUE: str = "orphan_open_issue"
SOURCE_IDLE: str = "idle"


# Priority ranking — lower number = higher priority.
PRIORITY_ORDER: Mapping[str, int] = {
    SOURCE_CI_FAILED_PR: 1,
    SOURCE_APPROVED_CODING_JOB: 2,
    SOURCE_UNRESOLVED_DISCUSSION: 3,
    SOURCE_ORPHAN_OPEN_ISSUE: 4,
    SOURCE_IDLE: 99,
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NextTaskCandidate:
    """A candidate the selector picked.

    ``payload`` is enough information for the caller to enqueue the
    correct downstream job — keys vary by source:

      * ``ci_failed_pr`` → ``pr_number`` / ``branch`` / ``head_sha`` / ``reason``
      * ``approved_coding_job`` → ``session_id`` / ``executor_role`` / ``coding_job`` (dict snapshot)
      * ``unresolved_discussion`` → ``session_id`` / ``thread_id`` / ``missing_roles``
      * ``orphan_open_issue`` → ``issue_number`` / ``title``
    """

    source: str
    priority: int
    reason: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    selected_at: str = ""

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "source": self.source,
            "priority": self.priority,
            "reason": self.reason,
            "payload": dict(self.payload),
            "selected_at": self.selected_at,
        }


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class GithubStateLike(Protocol):
    """Minimal surface the selector needs from the GitHub side.

    Production wires this through G6 LiveGithubAppClient. Tests pass
    a simple namespace / fake implementation.
    """

    def list_failed_ci_active_prs(self) -> Sequence[Mapping[str, Any]]:  # pragma: no cover
        ...

    def list_open_issues_without_session(self) -> Sequence[Mapping[str, Any]]:  # pragma: no cover
        ...


class SessionStateLike(Protocol):
    """Minimal surface for the Yule session-state side."""

    def list_approved_coding_jobs(self) -> Sequence[Mapping[str, Any]]:  # pragma: no cover
        ...

    def list_unresolved_discussion_threads(self) -> Sequence[Mapping[str, Any]]:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


def select_next_task(
    *,
    github_state: GithubStateLike,
    session_state: SessionStateLike,
    now: Optional[datetime] = None,
) -> NextTaskCandidate:
    """Run the 4-priority selector and return the picked candidate.

    Returns a :class:`NextTaskCandidate` with ``source = SOURCE_IDLE``
    when nothing matches — the caller's run-loop sleeps until the
    next tick.

    Order is *strict* — the first non-empty source wins. Within a
    source, the first row from the underlying ``list_*`` is taken;
    callers are responsible for sorting their results (e.g. oldest
    PR first).
    """

    when_iso = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()

    # 1. CI failed active PR — highest priority.
    failed_prs = list(github_state.list_failed_ci_active_prs() or ())
    if failed_prs:
        first = failed_prs[0]
        return NextTaskCandidate(
            source=SOURCE_CI_FAILED_PR,
            priority=PRIORITY_ORDER[SOURCE_CI_FAILED_PR],
            reason=(
                f"CI failed on PR #{first.get('pr_number')} "
                f"({first.get('reason') or 'unknown'}) — needs re-plan / retry"
            ),
            payload=dict(first),
            selected_at=when_iso,
        )

    # 2. Approved coding job ready to execute.
    approved = list(session_state.list_approved_coding_jobs() or ())
    if approved:
        first = approved[0]
        return NextTaskCandidate(
            source=SOURCE_APPROVED_CODING_JOB,
            priority=PRIORITY_ORDER[SOURCE_APPROVED_CODING_JOB],
            reason=(
                f"approved coding_job ready (session={first.get('session_id')}, "
                f"executor={first.get('executor_role')}) — eligible for "
                "coding_execute enqueue"
            ),
            payload=dict(first),
            selected_at=when_iso,
        )

    # 3. Unresolved discussion thread — needs another role take or
    #    research refresh.
    unresolved = list(session_state.list_unresolved_discussion_threads() or ())
    if unresolved:
        first = unresolved[0]
        return NextTaskCandidate(
            source=SOURCE_UNRESOLVED_DISCUSSION,
            priority=PRIORITY_ORDER[SOURCE_UNRESOLVED_DISCUSSION],
            reason=(
                f"unresolved discussion thread {first.get('thread_id')} "
                f"(session={first.get('session_id')}, "
                f"missing_roles={first.get('missing_roles')}) — eligible for "
                "role_take or research_collect"
            ),
            payload=dict(first),
            selected_at=when_iso,
        )

    # 4. Orphan open issue.
    orphans = list(github_state.list_open_issues_without_session() or ())
    if orphans:
        first = orphans[0]
        return NextTaskCandidate(
            source=SOURCE_ORPHAN_OPEN_ISSUE,
            priority=PRIORITY_ORDER[SOURCE_ORPHAN_OPEN_ISSUE],
            reason=(
                f"orphan open issue #{first.get('issue_number')} "
                f"({first.get('title')}) — no session attached, "
                "eligible for intake"
            ),
            payload=dict(first),
            selected_at=when_iso,
        )

    return NextTaskCandidate(
        source=SOURCE_IDLE,
        priority=PRIORITY_ORDER[SOURCE_IDLE],
        reason="no candidates — runtime idle",
        payload={},
        selected_at=when_iso,
    )


def stamp_selection_to_session_extra(
    extra: Mapping[str, Any],
    candidate: NextTaskCandidate,
    *,
    dispatched_at: Optional[str] = None,
) -> Mapping[str, Any]:
    """Return a new ``session.extra`` dict with the selector decision.

    Stores under ``session.extra.next_task_selection``:

      * ``source`` / ``priority`` / ``reason`` / ``payload`` / ``selected_at``
      * (when the caller actually enqueued the next job)
        ``dispatched_at`` ISO timestamp

    Pure — does not mutate input.
    """

    new_extra: dict = dict(extra or {})
    new_extra["next_task_selection"] = {
        **candidate.to_payload(),
        "dispatched_at": dispatched_at,
    }
    return new_extra


__all__ = (
    "GithubStateLike",
    "NextTaskCandidate",
    "PRIORITY_ORDER",
    "SOURCE_APPROVED_CODING_JOB",
    "SOURCE_CI_FAILED_PR",
    "SOURCE_IDLE",
    "SOURCE_ORPHAN_OPEN_ISSUE",
    "SOURCE_UNRESOLVED_DISCUSSION",
    "SessionStateLike",
    "select_next_task",
    "stamp_selection_to_session_extra",
)
