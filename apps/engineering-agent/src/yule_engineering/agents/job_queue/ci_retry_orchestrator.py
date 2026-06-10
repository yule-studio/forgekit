"""CI retry orchestrator — Round 3 of #73.

The Round 2 ``ci_status`` module is *pure* — it only computes verdicts
and partitions failed PRs. What was missing was the side-effect
layer that:

  * fetches the live CI status for a PR head_sha,
  * appends a retry-attempt row to ``session.extra``,
  * derives the standardised completion vocabulary
    (``done`` / ``retry_ready`` / ``blocked``),
  * for ``retry_ready``: requeues a fresh ``coding_execute`` row,
  * for ``done`` / ``blocked``: funnels through
    :func:`completion_hook.record_completion` so the next-task
    selector + audit log see a consistent terminal event.

Hard rails:

  * Max-attempts cap honoured by delegating every retry decision to
    :func:`ci_status.decide_retry`. Even if the orchestrator were
    misconfigured, the pure decider refuses to recommend more than
    ``policy.max_attempts`` retries.
  * ``protected branch`` / ``force push`` rejection stays on the
    worker side — the orchestrator's requeue path never sets either.
  * Unknown CI conclusion (no checks configured / API unreachable)
    escalates to ``blocked`` immediately so we don't spin while the
    PR is in an indeterminate state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

from .ci_status import (
    CIRetryPolicy,
    CIStatus,
    RetryAttemptLog,
    decide_retry,
    derive_completion_status_from_ci,
    from_check_runs,
    read_retry_log,
    record_retry_attempt,
)
from .coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    build_coding_execute_request,
    iter_ready_coding_jobs,
)
from .coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
)
from .completion_hook import (
    COMPLETION_BLOCKED,
    COMPLETION_DONE,
    COMPLETION_NEEDS_APPROVAL,
    COMPLETION_RETRY_READY,
    JobCompletionEvent,
    record_completion,
)


logger = logging.getLogger(__name__)


SESSION_EXTRA_PROGRESS_KEY: str = "coding_execute_progress"


# ---------------------------------------------------------------------------
# Live CI fetcher Protocol
# ---------------------------------------------------------------------------


class CIStatusFetcher(Protocol):
    """Translate (repo, pr_number, head_sha) → :class:`CIStatus`."""

    def fetch(
        self, *, repo: str, pr_number: int, head_sha: str
    ) -> CIStatus:  # pragma: no cover - Protocol
        ...


@dataclass(frozen=True)
class GithubAppCheckRunFetcher:
    """Default :class:`CIStatusFetcher` that wraps :class:`LiveGithubAppClient`.

    Calls ``list_check_runs`` then projects via
    :func:`ci_status.from_check_runs`. Failures bubble up as
    :class:`CIStatus` with ``conclusion="unknown"`` so the
    orchestrator can route them straight to the "blocked" branch
    without re-implementing aggregation.
    """

    live_client: Any

    def fetch(self, *, repo: str, pr_number: int, head_sha: str) -> CIStatus:
        try:
            runs = self.live_client.list_check_runs(repo=repo, head_sha=head_sha)
        except Exception:  # noqa: BLE001 - GitHub API hiccup → unknown
            logger.warning(
                "GithubAppCheckRunFetcher: list_check_runs raised for %s@%s",
                repo,
                head_sha[:10],
                exc_info=True,
            )
            return CIStatus(
                pr_number=pr_number, head_sha=head_sha, conclusion="unknown"
            )
        return from_check_runs(
            pr_number=pr_number, head_sha=head_sha, runs=tuple(runs or ())
        )


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CIRetryDecision:
    """Outcome of one orchestrator pass for a single PR.

    ``completion_status`` is the standard 4-state vocabulary the
    next-task selector consumes. ``requeued_job_id`` is non-empty
    when the orchestrator scheduled another ``coding_execute`` row.
    """

    pr_number: int
    head_sha: str
    completion_status: str
    reason: str
    attempt_count: int
    max_attempts: int
    wait_seconds: float = 0.0
    requeued_job_id: Optional[str] = None
    audit_entry_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(tz=timezone.utc)).replace(microsecond=0).isoformat()


def _persist_session_extra(
    session: Any,
    new_extra: Mapping[str, Any],
    *,
    update_session_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> None:
    """Write *new_extra* back to *session* via the workflow store.

    Failures log + return — the orchestrator's verdict is still
    returned to the caller so a stale session doesn't block the
    completion hook from firing.
    """

    if session is None:
        return
    try:
        from dataclasses import replace as _replace
        updated = _replace(session, extra=dict(new_extra))
    except Exception:  # noqa: BLE001
        return
    persist = update_session_fn or _default_update_session
    try:
        persist(updated, now=(now or datetime.now(tz=timezone.utc)))
    except Exception:  # noqa: BLE001
        logger.warning(
            "ci_retry_orchestrator: persisting session.extra raised", exc_info=True
        )


def _default_update_session(session: Any, *, now: datetime) -> Any:
    from ..workflow_state import update_session

    return update_session(session, now=now)


def _append_progress(
    extra: Mapping[str, Any],
    *,
    pr_number: int,
    head_sha: str,
    completion_status: str,
    reason: str,
    attempt_count: int,
    when_iso: str,
) -> Mapping[str, Any]:
    """Append a structured progress entry to ``coding_execute_progress``.

    The list is bounded to 50 entries so a long-running session
    doesn't grow ``session.extra`` unbounded. New entries land at
    the end (chronological).
    """

    base: dict = dict(extra or {})
    history_raw = base.get(SESSION_EXTRA_PROGRESS_KEY)
    history: list = list(history_raw) if isinstance(history_raw, (list, tuple)) else []
    history.append(
        {
            "pr_number": pr_number,
            "head_sha": head_sha,
            "completion_status": completion_status,
            "reason": reason,
            "attempt_count": attempt_count,
            "at": when_iso,
        }
    )
    if len(history) > 50:
        history = history[-50:]
    base[SESSION_EXTRA_PROGRESS_KEY] = history
    return base


# ---------------------------------------------------------------------------
# Requeue helper
# ---------------------------------------------------------------------------


def _requeue_coding_execute(
    *,
    session: Any,
    coding_job: Mapping[str, Any],
    worker: CodingExecutorWorker,
    env: Optional[Mapping[str, str]],
    now: Optional[datetime],
) -> Optional[str]:
    """Schedule another ``coding_execute`` attempt for *session*.

    Builds the request from the persisted coding_job (same path the
    dispatcher uses) but bumps ``branch_hint`` with an ``-attemptN``
    suffix so the worker doesn't dedup against the prior in-flight
    branch. Returns the new ``job_id`` or None on failure.

    The branch-suffix bump is the **only** mutation — write/forbidden
    scope and safety rules carry through unchanged.
    """

    from .coding_execute_dispatcher import ReadyCodingJob

    ready = ReadyCodingJob(
        session=session,
        coding_job=dict(coding_job),
        session_id=str(coding_job.get("session_id") or getattr(session, "session_id", "")),
    )
    try:
        request = build_coding_execute_request(ready, env=env)
    except Exception:  # noqa: BLE001
        logger.warning(
            "ci_retry_orchestrator: build_coding_execute_request raised",
            exc_info=True,
        )
        return None

    bumped = _bump_branch_hint(request, session)
    try:
        job, _created = worker.enqueue(
            bumped, now=(now.timestamp() if now else None)
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "ci_retry_orchestrator: requeue worker.enqueue raised",
            exc_info=True,
        )
        return None
    return job.job_id


def _bump_branch_hint(
    request: CodingExecuteRequest, session: Any
) -> CodingExecuteRequest:
    """Append ``-attemptN`` to the branch hint so the worker dedup
    treats it as a fresh attempt.

    Counts the existing progress entries for the same PR + uses the
    next index. Falls back to a UTC-second suffix when no progress
    history is present (shouldn't happen in production, but keeps
    the function pure).
    """

    extra = getattr(session, "extra", None) or {}
    history = extra.get(SESSION_EXTRA_PROGRESS_KEY) if isinstance(extra, Mapping) else ()
    attempts = 1
    if isinstance(history, (list, tuple)):
        attempts = max(1, len(history) + 1)
    base = request.branch_hint or ""
    if not base:
        from dataclasses import replace as _replace
        return _replace(request, branch_hint=f"agent/{request.executor_role}/retry-{attempts}")
    # Strip prior ``-attemptN`` suffix so we don't get nested suffixes
    # on the third / fourth retry.
    if "-attempt" in base:
        base = base.rsplit("-attempt", 1)[0]
    new_hint = f"{base}-attempt{attempts}"
    from dataclasses import replace as _replace
    return _replace(request, branch_hint=new_hint)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def orchestrate_ci_retry(
    *,
    session: Any,
    pr_number: int,
    head_sha: str,
    repo: str,
    fetcher: CIStatusFetcher,
    worker: CodingExecutorWorker,
    policy: Optional[CIRetryPolicy] = None,
    update_session_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    progress_post_fn: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> CIRetryDecision:
    """Drive one CI poll → verdict → side-effect cycle.

    Returns a :class:`CIRetryDecision` describing what was done.
    Side effects:

      * ``session.extra['ci_retry_logs'][pr_number]`` extended.
      * ``session.extra['coding_execute_progress']`` appended.
      * On ``retry_ready``: a new ``coding_execute`` row queued
        (idempotent via the worker's ``find_active`` dedup).
      * On any terminal status: :func:`completion_hook.record_completion`
        invoked + audit entry stamped onto session.extra.
      * Optional :func:`progress_post_fn` called so a Discord / PR
        comment can be sent. The post is best-effort — failures
        log but do not change the verdict.
    """

    pol = policy or CIRetryPolicy()
    extra = dict(getattr(session, "extra", None) or {})
    coding_job = extra.get("coding_job") if isinstance(extra, Mapping) else None
    if not isinstance(coding_job, Mapping):
        coding_job = {}

    # 1. Fetch CI status.
    status = fetcher.fetch(repo=repo, pr_number=pr_number, head_sha=head_sha)

    # 2. Read prior retry log + record this attempt.
    log = read_retry_log(extra, pr_number=pr_number)
    extra = dict(record_retry_attempt(
        extra,
        pr_number=pr_number,
        head_sha=head_sha,
        reason=("ci_failure" if status.is_failure() else status.conclusion),
        when=_now_iso(now),
    ))

    # 3. Decide retry / done / blocked.
    verdict = decide_retry(status=status, log=log, policy=pol)
    completion_status = derive_completion_status_from_ci(
        status=status, log=log, policy=pol
    )

    # 4. Stamp progress + maybe requeue.
    when_iso = _now_iso(now)
    extra = dict(_append_progress(
        extra,
        pr_number=pr_number,
        head_sha=head_sha,
        completion_status=completion_status,
        reason=verdict.reason,
        attempt_count=log.attempts + 1,
        when_iso=when_iso,
    ))

    requeued_job_id: Optional[str] = None
    if verdict.should_retry:
        requeued_job_id = _requeue_coding_execute(
            session=session,
            coding_job=coding_job,
            worker=worker,
            env=env,
            now=now,
        )
        # On a successful requeue, drop the stale dispatch marker so
        # the dispatcher / selector re-pick fresh on the next tick.
        if requeued_job_id is not None:
            extra.pop(SESSION_EXTRA_DISPATCH_KEY, None)

    # 5. Funnel through the completion hook for terminal states.
    audit_entry_id: Optional[str] = None
    if completion_status in {COMPLETION_DONE, COMPLETION_BLOCKED, COMPLETION_NEEDS_APPROVAL}:
        event = JobCompletionEvent(
            job_id=str(coding_job.get("session_id") or getattr(session, "session_id", "")),
            job_type="coding_execute",
            session_id=str(getattr(session, "session_id", "") or coding_job.get("session_id") or ""),
            status=completion_status,
            reason=verdict.reason,
            metadata={
                "pr_number": pr_number,
                "head_sha": head_sha,
                "attempts": log.attempts + 1,
                "ci_conclusion": status.conclusion,
            },
            completed_at=when_iso,
        )
        new_extra, routing = record_completion(event=event, session_extra=extra)
        extra = dict(new_extra)
        audit_entry_id = routing.audit_entry_id

    # 6. Persist + optional progress post.
    _persist_session_extra(
        session, extra, update_session_fn=update_session_fn, now=now
    )

    if progress_post_fn is not None:
        try:
            progress_post_fn(
                pr_number=pr_number,
                head_sha=head_sha,
                completion_status=completion_status,
                reason=verdict.reason,
                attempt_count=log.attempts + 1,
                requeued_job_id=requeued_job_id,
            )
        except Exception:  # noqa: BLE001 - post is best-effort
            logger.warning(
                "ci_retry_orchestrator: progress_post_fn raised", exc_info=True
            )

    return CIRetryDecision(
        pr_number=pr_number,
        head_sha=head_sha,
        completion_status=completion_status,
        reason=verdict.reason,
        attempt_count=log.attempts + 1,
        max_attempts=pol.max_attempts,
        wait_seconds=verdict.wait_seconds,
        requeued_job_id=requeued_job_id,
        audit_entry_id=audit_entry_id,
    )


__all__ = (
    "CIRetryDecision",
    "CIStatusFetcher",
    "GithubAppCheckRunFetcher",
    "SESSION_EXTRA_PROGRESS_KEY",
    "orchestrate_ci_retry",
)
