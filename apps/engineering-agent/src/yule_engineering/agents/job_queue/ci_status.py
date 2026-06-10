"""CI status + retry policy — Round 2 of #73.

The `coding_execute` worker pushes a draft PR; GitHub's check
runs then decide success / failure. This module turns those check
results into:

  1. A normalised :class:`CIStatus` (success / failure / pending /
     unknown) that's stable regardless of which provider ran the
     suite (Actions, CircleCI, etc.).
  2. A :class:`RetryAttemptLog` stamped on ``session.extra`` so the
     selector can count attempts per PR / branch.
  3. A :class:`RetryVerdict` from :func:`decide_retry` — combining
     status + log + policy → "retry vs escalate" decision.
  4. A bridge to the standard 4-state completion vocabulary
     (`done` / `retry_ready` / `blocked`) via
     :func:`derive_completion_status_from_ci`.

Hard rails:

  * No infinite retry. Default policy caps at 3 attempts; once
    exhausted, ``decide_retry`` flips to ``escalation_status="blocked"``
    and the selector stops re-picking the PR.
  * Retry decisions are pure functions — no I/O. The caller is
    responsible for fetching the CI status and persisting the log.
  * Same-head_sha re-runs DO consume an attempt slot (to guard
    against a runner re-spawning the same broken commit forever).
    A new head_sha (operator pushed a fix) does NOT consume — the
    selector treats it as a fresh attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)


# ---------------------------------------------------------------------------
# CI conclusion vocabulary
# ---------------------------------------------------------------------------


CI_SUCCESS: str = "success"
CI_FAILURE: str = "failure"
CI_CANCELLED: str = "cancelled"
CI_TIMED_OUT: str = "timed_out"
CI_PENDING: str = "pending"
CI_UNKNOWN: str = "unknown"


# Map raw GitHub Check Run "conclusion" strings to our normalised set.
# GitHub uses these conclusion values:
#   success / failure / neutral / cancelled / timed_out / action_required
#   / stale / skipped — plus null while pending.
_CONCLUSION_MAP: Mapping[str, str] = {
    "success": CI_SUCCESS,
    "neutral": CI_SUCCESS,
    "skipped": CI_SUCCESS,
    "failure": CI_FAILURE,
    "action_required": CI_FAILURE,
    "cancelled": CI_CANCELLED,
    "timed_out": CI_TIMED_OUT,
    "stale": CI_UNKNOWN,
}


@dataclass(frozen=True)
class CIStatus:
    """Aggregate CI result for one PR head_sha."""

    pr_number: int
    head_sha: str
    conclusion: str
    fetched_at: str = ""
    failing_runs: Tuple[str, ...] = ()
    pending_runs: Tuple[str, ...] = ()

    def is_failure(self) -> bool:
        return self.conclusion in (CI_FAILURE, CI_CANCELLED, CI_TIMED_OUT)

    def is_success(self) -> bool:
        return self.conclusion == CI_SUCCESS

    def is_pending(self) -> bool:
        return self.conclusion == CI_PENDING


def from_check_runs(
    *,
    pr_number: int,
    head_sha: str,
    runs: Sequence[Mapping[str, Any]],
    fetched_at: Optional[str] = None,
) -> CIStatus:
    """Aggregate a sequence of GitHub Check Runs into a single CIStatus.

    Aggregation rule (mirrors the GitHub UI's "all checks have passed"
    badge):

      * Any run with ``status != "completed"`` → overall = ``pending``.
      * Else: any failure-class conclusion → overall = ``failure``
        (cancellation / timed_out preserved as their own conclusion
        if they are the *only* non-success result).
      * Else: ``success``.

    Empty runs list → ``unknown`` (we don't know if CI is configured;
    the caller decides whether to treat that as failure).
    """

    when = fetched_at or _iso_now()
    failing: list = []
    pending: list = []
    if not runs:
        return CIStatus(
            pr_number=pr_number,
            head_sha=head_sha,
            conclusion=CI_UNKNOWN,
            fetched_at=when,
        )
    has_pending = False
    has_failure = False
    has_cancelled = False
    has_timed_out = False
    for run in runs:
        name = str(run.get("name") or run.get("check_run_name") or "(unnamed)")
        status = (run.get("status") or "").lower()
        conclusion = (run.get("conclusion") or "").lower()
        if status and status != "completed":
            has_pending = True
            pending.append(name)
            continue
        normalised = _CONCLUSION_MAP.get(conclusion, CI_UNKNOWN)
        if normalised == CI_FAILURE:
            has_failure = True
            failing.append(name)
        elif normalised == CI_CANCELLED:
            has_cancelled = True
            failing.append(name)
        elif normalised == CI_TIMED_OUT:
            has_timed_out = True
            failing.append(name)
        # success / neutral / skipped contribute no failure signal.
    if has_pending and not (has_failure or has_cancelled or has_timed_out):
        overall = CI_PENDING
    elif has_failure:
        overall = CI_FAILURE
    elif has_timed_out:
        overall = CI_TIMED_OUT
    elif has_cancelled:
        overall = CI_CANCELLED
    elif has_pending:
        # Mixed state — pending plus a failure already captured above
        # is handled, so this branch only runs when has_pending and a
        # success-only set; treat the failure-mixed case as failure.
        overall = CI_PENDING
    else:
        overall = CI_SUCCESS
    return CIStatus(
        pr_number=pr_number,
        head_sha=head_sha,
        conclusion=overall,
        fetched_at=when,
        failing_runs=tuple(failing),
        pending_runs=tuple(pending),
    )


# ---------------------------------------------------------------------------
# Retry policy + log
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CIRetryPolicy:
    """How aggressive the retry loop is.

    Defaults: 3 attempts, base 60 s, ×2 backoff. Operator can tighten
    in .env for cost-sensitive accounts.
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 60.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 1800.0  # cap at 30 min so the loop never sleeps for hours

    def backoff_for(self, attempt: int) -> float:
        """Seconds to wait before *attempt* (1-indexed).

        Attempt 1 → no wait (first run).
        Attempt 2 → base.
        Attempt 3 → base × multiplier.
        ...capped at ``max_backoff_seconds``.
        """

        if attempt <= 1:
            return 0.0
        wait = self.base_backoff_seconds * (
            self.backoff_multiplier ** (attempt - 2)
        )
        return min(self.max_backoff_seconds, wait)


@dataclass(frozen=True)
class RetryAttemptLog:
    """How many times we've retried CI on this PR + recent history.

    ``head_sha_history`` keeps the head SHA of each attempt so a
    later push of a fix (new SHA) can reset / extend the retry budget
    if policy allows.
    """

    pr_number: int
    attempts: int = 0
    last_attempt_at: Optional[str] = None
    last_failure_reason: Optional[str] = None
    head_sha_history: Tuple[str, ...] = ()

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "pr_number": self.pr_number,
            "attempts": self.attempts,
            "last_attempt_at": self.last_attempt_at,
            "last_failure_reason": self.last_failure_reason,
            "head_sha_history": list(self.head_sha_history),
        }

    @classmethod
    def from_payload(cls, payload: Optional[Mapping[str, Any]]) -> "RetryAttemptLog":
        if not payload:
            return cls(pr_number=0)
        history = payload.get("head_sha_history") or ()
        return cls(
            pr_number=int(payload.get("pr_number") or 0),
            attempts=int(payload.get("attempts") or 0),
            last_attempt_at=payload.get("last_attempt_at"),
            last_failure_reason=payload.get("last_failure_reason"),
            head_sha_history=tuple(str(h) for h in history),
        )

    def appended(
        self, *, head_sha: str, reason: Optional[str], when: str
    ) -> "RetryAttemptLog":
        return RetryAttemptLog(
            pr_number=self.pr_number,
            attempts=self.attempts + 1,
            last_attempt_at=when,
            last_failure_reason=reason,
            head_sha_history=tuple(self.head_sha_history) + (head_sha,),
        )


@dataclass(frozen=True)
class RetryVerdict:
    """Outcome of :func:`decide_retry`.

    ``should_retry`` True → enqueue another coding_execute attempt
    after ``wait_seconds``. False → stop; ``escalation_status`` is one
    of the standard 4 (`blocked` / `done`); the selector won't re-pick.
    """

    should_retry: bool
    reason: str
    next_attempt: int
    wait_seconds: float = 0.0
    escalation_status: Optional[str] = None
    completion_status: Optional[str] = None  # `retry_ready` / `blocked` / `done`

    def to_payload(self) -> Mapping[str, Any]:
        return {
            "should_retry": self.should_retry,
            "reason": self.reason,
            "next_attempt": self.next_attempt,
            "wait_seconds": self.wait_seconds,
            "escalation_status": self.escalation_status,
            "completion_status": self.completion_status,
        }


def decide_retry(
    *,
    status: CIStatus,
    log: RetryAttemptLog,
    policy: Optional[CIRetryPolicy] = None,
) -> RetryVerdict:
    """Combine CI verdict + attempt log → retry / escalate decision.

    Hard rails:

      * Success → ``should_retry=False``, ``completion_status="done"``.
        No more attempts even if log.attempts < max.
      * Pending → ``should_retry=False``, ``completion_status=None``
        (selector should NOT pick this PR yet — wait for CI to finish).
      * Unknown → treated as ``blocked`` (CI not configured / API
        couldn't reach GitHub). Caller decides whether to alert.
      * Failure when log.attempts < max_attempts → retry.
      * Failure when log.attempts >= max_attempts → ``blocked``.
    """

    pol = policy or CIRetryPolicy()
    if status.is_success():
        return RetryVerdict(
            should_retry=False,
            reason="ci passed — coding_execute terminal done",
            next_attempt=log.attempts,
            completion_status="done",
        )
    if status.is_pending():
        return RetryVerdict(
            should_retry=False,
            reason="ci still pending — selector defers until run completes",
            next_attempt=log.attempts,
            completion_status=None,
        )
    if status.conclusion == CI_UNKNOWN:
        return RetryVerdict(
            should_retry=False,
            reason=(
                "ci status unknown (no check runs / api unreachable) — "
                "operator inspection required"
            ),
            next_attempt=log.attempts,
            escalation_status="blocked",
            completion_status="blocked",
        )
    # Failure-class conclusion.
    if log.attempts >= pol.max_attempts:
        return RetryVerdict(
            should_retry=False,
            reason=(
                f"ci {status.conclusion} after {log.attempts} attempts "
                f"(>= max {pol.max_attempts}) — escalate to blocked, "
                "operator review required"
            ),
            next_attempt=log.attempts,
            escalation_status="blocked",
            completion_status="blocked",
        )
    next_attempt = log.attempts + 1
    return RetryVerdict(
        should_retry=True,
        reason=(
            f"ci {status.conclusion} on attempt {log.attempts}/{pol.max_attempts} "
            f"({len(status.failing_runs)} failing run(s)) — schedule retry"
        ),
        next_attempt=next_attempt,
        wait_seconds=pol.backoff_for(next_attempt),
        completion_status="retry_ready",
    )


# ---------------------------------------------------------------------------
# session.extra helpers (pure dict transforms — caller persists)
# ---------------------------------------------------------------------------


_RETRY_LOG_KEY: str = "ci_retry_logs"


def read_retry_log(
    session_extra: Optional[Mapping[str, Any]],
    *,
    pr_number: int,
) -> RetryAttemptLog:
    """Pull the per-PR retry log out of session.extra.

    Returns an empty log (`attempts=0`) when absent — the caller
    should never have to special-case "no log yet".
    """

    if not session_extra:
        return RetryAttemptLog(pr_number=pr_number)
    logs = session_extra.get(_RETRY_LOG_KEY) or {}
    payload = logs.get(str(pr_number)) if isinstance(logs, Mapping) else None
    if not payload:
        return RetryAttemptLog(pr_number=pr_number)
    log = RetryAttemptLog.from_payload(payload)
    if log.pr_number == 0:
        # Older entries may be missing pr_number; backfill from key.
        return RetryAttemptLog(
            pr_number=pr_number,
            attempts=log.attempts,
            last_attempt_at=log.last_attempt_at,
            last_failure_reason=log.last_failure_reason,
            head_sha_history=log.head_sha_history,
        )
    return log


def record_retry_attempt(
    session_extra: Optional[Mapping[str, Any]],
    *,
    pr_number: int,
    head_sha: str,
    reason: Optional[str] = None,
    when: Optional[str] = None,
) -> Mapping[str, Any]:
    """Append a new attempt row to the per-PR retry log.

    Pure transform — returns a fresh ``session.extra`` dict the caller
    persists. Uses ``str(pr_number)`` as the key so JSON round-trips
    cleanly (sqlite stores extra as JSON text).
    """

    when_iso = when or _iso_now()
    base: dict = dict(session_extra or {})
    logs_raw = base.get(_RETRY_LOG_KEY)
    logs: dict = dict(logs_raw) if isinstance(logs_raw, Mapping) else {}
    current = read_retry_log(base, pr_number=pr_number)
    next_log = current.appended(head_sha=head_sha, reason=reason, when=when_iso)
    logs[str(pr_number)] = dict(next_log.to_payload())
    base[_RETRY_LOG_KEY] = logs
    return base


# ---------------------------------------------------------------------------
# Bridge to completion vocabulary
# ---------------------------------------------------------------------------


def derive_completion_status_from_ci(
    *,
    status: CIStatus,
    log: RetryAttemptLog,
    policy: Optional[CIRetryPolicy] = None,
) -> str:
    """Map a CI status + retry log to one of `done` / `retry_ready` / `blocked`.

    Pending is *not* a terminal state, so when the CI is still running
    the caller should NOT funnel through completion_hook yet. As a
    safety, we map pending → ``retry_ready`` (the worker keeps the
    coding_execute job alive for another tick) but emit a clear reason.
    """

    verdict = decide_retry(status=status, log=log, policy=policy)
    if verdict.completion_status:
        return verdict.completion_status
    # Pending — keep the job alive but don't escalate yet.
    return "retry_ready"


# ---------------------------------------------------------------------------
# Selector integration — filters CI-failed PRs that exhausted attempts
# ---------------------------------------------------------------------------


def partition_failed_prs_by_retry(
    failed_prs: Iterable[Mapping[str, Any]],
    *,
    retry_lookup,
    policy: Optional[CIRetryPolicy] = None,
) -> Tuple[list, list]:
    """Split a sequence of failed-PR rows into ``retryable`` / ``escalated``.

    *retry_lookup* is called with ``pr_number`` and must return a
    :class:`RetryAttemptLog` for that PR. Callers will typically wrap
    a ``workflow_state.get_session(pr_session_id).extra`` lookup here.

    Returns ``(retryable, escalated)``:
      * ``retryable`` — rows the next-task selector may pick.
      * ``escalated`` — rows whose retry budget is exhausted; selector
        skips and the operator surface picks them up via the regular
        blocked-job notification channel.
    """

    pol = policy or CIRetryPolicy()
    retryable: list = []
    escalated: list = []
    for row in failed_prs:
        pr_number = int(row.get("pr_number") or 0)
        log = retry_lookup(pr_number) or RetryAttemptLog(pr_number=pr_number)
        if log.attempts >= pol.max_attempts:
            escalated.append(
                {
                    **dict(row),
                    "ci_retry_attempts": log.attempts,
                    "ci_retry_max": pol.max_attempts,
                    "ci_retry_status": "escalated",
                }
            )
        else:
            retryable.append(
                {
                    **dict(row),
                    "ci_retry_attempts": log.attempts,
                    "ci_retry_max": pol.max_attempts,
                    "ci_retry_status": "retryable",
                }
            )
    return retryable, escalated


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


__all__ = (
    "CIRetryPolicy",
    "CIStatus",
    "CI_CANCELLED",
    "CI_FAILURE",
    "CI_PENDING",
    "CI_SUCCESS",
    "CI_TIMED_OUT",
    "CI_UNKNOWN",
    "RetryAttemptLog",
    "RetryVerdict",
    "decide_retry",
    "derive_completion_status_from_ci",
    "from_check_runs",
    "partition_failed_prs_by_retry",
    "read_retry_log",
    "record_retry_attempt",
)
