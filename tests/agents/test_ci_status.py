"""ci_status — Round 2 of #73.

Pin the CI failure → retry loop:

  * Aggregation of GitHub Check Runs into a single :class:`CIStatus`.
  * Retry policy / attempt-log persistence on session.extra.
  * :func:`decide_retry` flipping retry → blocked once max_attempts hit
    (no infinite retry).
  * Bridge to the standard 4-state completion vocabulary
    (``done`` / ``retry_ready`` / ``blocked``).
  * Selector integration via :func:`partition_failed_prs_by_retry`
    and :func:`select_next_task_with_ci_retry_guard`.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.ci_status import (
    CI_CANCELLED,
    CI_FAILURE,
    CI_PENDING,
    CI_SUCCESS,
    CI_TIMED_OUT,
    CI_UNKNOWN,
    CIRetryPolicy,
    CIStatus,
    RetryAttemptLog,
    decide_retry,
    derive_completion_status_from_ci,
    from_check_runs,
    partition_failed_prs_by_retry,
    read_retry_log,
    record_retry_attempt,
)
from yule_engineering.agents.job_queue.next_task_selector import (
    SOURCE_APPROVED_CODING_JOB,
    SOURCE_CI_FAILED_PR,
    SOURCE_IDLE,
    select_next_task_with_ci_retry_guard,
)


# ---------------------------------------------------------------------------
# from_check_runs aggregation
# ---------------------------------------------------------------------------


class FromCheckRunsTests(unittest.TestCase):
    def test_empty_runs_is_unknown(self) -> None:
        status = from_check_runs(pr_number=1, head_sha="abc", runs=[])
        self.assertEqual(status.conclusion, CI_UNKNOWN)

    def test_all_success_is_success(self) -> None:
        status = from_check_runs(
            pr_number=1,
            head_sha="abc",
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "success"},
                {"name": "skipped-job", "status": "completed", "conclusion": "skipped"},
                {"name": "neutral-job", "status": "completed", "conclusion": "neutral"},
            ],
        )
        self.assertTrue(status.is_success())
        self.assertEqual(status.failing_runs, ())
        self.assertEqual(status.pending_runs, ())

    def test_one_failure_is_failure(self) -> None:
        status = from_check_runs(
            pr_number=2,
            head_sha="def",
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "failure"},
            ],
        )
        self.assertEqual(status.conclusion, CI_FAILURE)
        self.assertTrue(status.is_failure())
        self.assertIn("test", status.failing_runs)

    def test_action_required_counts_as_failure(self) -> None:
        status = from_check_runs(
            pr_number=3,
            head_sha="abc",
            runs=[
                {
                    "name": "review",
                    "status": "completed",
                    "conclusion": "action_required",
                },
            ],
        )
        self.assertEqual(status.conclusion, CI_FAILURE)

    def test_only_cancelled_is_cancelled(self) -> None:
        status = from_check_runs(
            pr_number=3,
            head_sha="abc",
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "cancelled"},
            ],
        )
        self.assertEqual(status.conclusion, CI_CANCELLED)
        self.assertTrue(status.is_failure())

    def test_only_timed_out_is_timed_out(self) -> None:
        status = from_check_runs(
            pr_number=4,
            head_sha="abc",
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "timed_out"},
            ],
        )
        self.assertEqual(status.conclusion, CI_TIMED_OUT)

    def test_pending_with_no_failure_is_pending(self) -> None:
        status = from_check_runs(
            pr_number=5,
            head_sha="abc",
            runs=[
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "queued"},
            ],
        )
        self.assertEqual(status.conclusion, CI_PENDING)
        self.assertIn("test", status.pending_runs)

    def test_pending_plus_failure_is_failure(self) -> None:
        status = from_check_runs(
            pr_number=6,
            head_sha="abc",
            runs=[
                {"name": "test", "status": "completed", "conclusion": "failure"},
                {"name": "deploy", "status": "queued"},
            ],
        )
        self.assertEqual(status.conclusion, CI_FAILURE)


# ---------------------------------------------------------------------------
# CIRetryPolicy.backoff_for
# ---------------------------------------------------------------------------


class BackoffTests(unittest.TestCase):
    def test_first_attempt_no_wait(self) -> None:
        pol = CIRetryPolicy()
        self.assertEqual(pol.backoff_for(1), 0.0)

    def test_second_attempt_uses_base(self) -> None:
        pol = CIRetryPolicy(base_backoff_seconds=60.0, backoff_multiplier=2.0)
        self.assertEqual(pol.backoff_for(2), 60.0)

    def test_third_attempt_multiplies(self) -> None:
        pol = CIRetryPolicy(base_backoff_seconds=60.0, backoff_multiplier=2.0)
        self.assertEqual(pol.backoff_for(3), 120.0)

    def test_backoff_capped_at_max(self) -> None:
        pol = CIRetryPolicy(
            base_backoff_seconds=60.0,
            backoff_multiplier=10.0,
            max_backoff_seconds=300.0,
        )
        # attempt 4 → 60 * 100 = 6000 → capped at 300.
        self.assertEqual(pol.backoff_for(4), 300.0)


# ---------------------------------------------------------------------------
# decide_retry
# ---------------------------------------------------------------------------


def _failed_status(pr: int = 7) -> CIStatus:
    return CIStatus(
        pr_number=pr,
        head_sha="sha-1",
        conclusion=CI_FAILURE,
        failing_runs=("test",),
    )


class DecideRetryTests(unittest.TestCase):
    def test_success_returns_done_no_retry(self) -> None:
        status = CIStatus(pr_number=1, head_sha="abc", conclusion=CI_SUCCESS)
        verdict = decide_retry(status=status, log=RetryAttemptLog(pr_number=1))
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.completion_status, "done")

    def test_pending_defers(self) -> None:
        status = CIStatus(pr_number=1, head_sha="abc", conclusion=CI_PENDING)
        verdict = decide_retry(status=status, log=RetryAttemptLog(pr_number=1))
        self.assertFalse(verdict.should_retry)
        # Pending → no terminal completion_status yet (the worker keeps
        # the job alive instead of escalating).
        self.assertIsNone(verdict.completion_status)

    def test_unknown_escalates_to_blocked(self) -> None:
        status = CIStatus(pr_number=1, head_sha="abc", conclusion=CI_UNKNOWN)
        verdict = decide_retry(status=status, log=RetryAttemptLog(pr_number=1))
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.escalation_status, "blocked")
        self.assertEqual(verdict.completion_status, "blocked")

    def test_first_failure_schedules_retry_with_backoff(self) -> None:
        verdict = decide_retry(
            status=_failed_status(),
            log=RetryAttemptLog(pr_number=7, attempts=0),
            policy=CIRetryPolicy(max_attempts=3, base_backoff_seconds=60.0),
        )
        self.assertTrue(verdict.should_retry)
        self.assertEqual(verdict.next_attempt, 1)
        # next_attempt=1 → no wait yet (first run not a retry).
        self.assertEqual(verdict.wait_seconds, 0.0)
        self.assertEqual(verdict.completion_status, "retry_ready")

    def test_second_failure_uses_backoff(self) -> None:
        verdict = decide_retry(
            status=_failed_status(),
            log=RetryAttemptLog(pr_number=7, attempts=1),
            policy=CIRetryPolicy(max_attempts=3, base_backoff_seconds=60.0),
        )
        self.assertTrue(verdict.should_retry)
        self.assertEqual(verdict.next_attempt, 2)
        self.assertEqual(verdict.wait_seconds, 60.0)
        self.assertEqual(verdict.completion_status, "retry_ready")

    def test_max_attempts_reached_escalates_to_blocked(self) -> None:
        # 3 attempts already recorded; another failure must escalate.
        verdict = decide_retry(
            status=_failed_status(),
            log=RetryAttemptLog(pr_number=7, attempts=3),
            policy=CIRetryPolicy(max_attempts=3),
        )
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.escalation_status, "blocked")
        self.assertEqual(verdict.completion_status, "blocked")
        self.assertIn("3 attempts", verdict.reason)

    def test_no_infinite_retry_with_zero_max_attempts(self) -> None:
        # Defensive: a misconfigured policy of 0 still escalates immediately.
        verdict = decide_retry(
            status=_failed_status(),
            log=RetryAttemptLog(pr_number=7, attempts=0),
            policy=CIRetryPolicy(max_attempts=0),
        )
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.completion_status, "blocked")


# ---------------------------------------------------------------------------
# session.extra round-trip helpers
# ---------------------------------------------------------------------------


class RetryLogPersistenceTests(unittest.TestCase):
    def test_read_returns_empty_log_when_absent(self) -> None:
        log = read_retry_log({}, pr_number=42)
        self.assertEqual(log.attempts, 0)
        self.assertEqual(log.pr_number, 42)

    def test_record_attempt_appends_history(self) -> None:
        extra = record_retry_attempt(
            None,
            pr_number=42,
            head_sha="aaa",
            reason="lint failed",
            when="2026-05-08T00:00:00+00:00",
        )
        log = read_retry_log(extra, pr_number=42)
        self.assertEqual(log.attempts, 1)
        self.assertEqual(log.head_sha_history, ("aaa",))
        self.assertEqual(log.last_failure_reason, "lint failed")
        self.assertEqual(log.last_attempt_at, "2026-05-08T00:00:00+00:00")

    def test_multiple_attempts_increment_counter(self) -> None:
        extra = None
        for sha, when in (
            ("aaa", "2026-05-08T01:00:00+00:00"),
            ("bbb", "2026-05-08T02:00:00+00:00"),
            ("ccc", "2026-05-08T03:00:00+00:00"),
        ):
            extra = record_retry_attempt(
                extra,
                pr_number=42,
                head_sha=sha,
                reason="failed",
                when=when,
            )
        log = read_retry_log(extra, pr_number=42)
        self.assertEqual(log.attempts, 3)
        self.assertEqual(log.head_sha_history, ("aaa", "bbb", "ccc"))

    def test_distinct_pr_numbers_have_independent_logs(self) -> None:
        extra = record_retry_attempt(
            None, pr_number=42, head_sha="aaa", when="t1"
        )
        extra = record_retry_attempt(
            extra, pr_number=43, head_sha="bbb", when="t2"
        )
        log42 = read_retry_log(extra, pr_number=42)
        log43 = read_retry_log(extra, pr_number=43)
        self.assertEqual(log42.attempts, 1)
        self.assertEqual(log43.attempts, 1)
        self.assertEqual(log42.head_sha_history, ("aaa",))
        self.assertEqual(log43.head_sha_history, ("bbb",))

    def test_extra_round_trips_unrelated_keys(self) -> None:
        extra = record_retry_attempt(
            {"unrelated": "value"},
            pr_number=42,
            head_sha="aaa",
            when="t",
        )
        self.assertEqual(extra["unrelated"], "value")
        self.assertIn("ci_retry_logs", extra)


# ---------------------------------------------------------------------------
# derive_completion_status_from_ci
# ---------------------------------------------------------------------------


class DeriveCompletionStatusTests(unittest.TestCase):
    def test_success_to_done(self) -> None:
        status = CIStatus(pr_number=1, head_sha="abc", conclusion=CI_SUCCESS)
        self.assertEqual(
            derive_completion_status_from_ci(
                status=status, log=RetryAttemptLog(pr_number=1)
            ),
            "done",
        )

    def test_failure_under_budget_is_retry_ready(self) -> None:
        self.assertEqual(
            derive_completion_status_from_ci(
                status=_failed_status(),
                log=RetryAttemptLog(pr_number=7, attempts=1),
                policy=CIRetryPolicy(max_attempts=3),
            ),
            "retry_ready",
        )

    def test_failure_over_budget_is_blocked(self) -> None:
        self.assertEqual(
            derive_completion_status_from_ci(
                status=_failed_status(),
                log=RetryAttemptLog(pr_number=7, attempts=3),
                policy=CIRetryPolicy(max_attempts=3),
            ),
            "blocked",
        )

    def test_pending_keeps_job_alive(self) -> None:
        status = CIStatus(pr_number=1, head_sha="abc", conclusion=CI_PENDING)
        # Pending → caller should NOT escalate; we map to retry_ready
        # so the job stays in the queue for another tick.
        self.assertEqual(
            derive_completion_status_from_ci(
                status=status, log=RetryAttemptLog(pr_number=1)
            ),
            "retry_ready",
        )


# ---------------------------------------------------------------------------
# Selector integration
# ---------------------------------------------------------------------------


class _FakeGithubState:
    def __init__(
        self,
        *,
        failed_prs: Sequence[Mapping[str, Any]],
        orphan_issues: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._failed = list(failed_prs)
        self._orphans = list(orphan_issues)

    def list_failed_ci_active_prs(self) -> Sequence[Mapping[str, Any]]:
        return list(self._failed)

    def list_open_issues_without_session(self) -> Sequence[Mapping[str, Any]]:
        return list(self._orphans)


class _FakeSessionState:
    def __init__(
        self,
        *,
        approved: Sequence[Mapping[str, Any]] = (),
        unresolved: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._approved = list(approved)
        self._unresolved = list(unresolved)

    def list_approved_coding_jobs(self) -> Sequence[Mapping[str, Any]]:
        return list(self._approved)

    def list_unresolved_discussion_threads(self) -> Sequence[Mapping[str, Any]]:
        return list(self._unresolved)


class PartitionFailedPRsTests(unittest.TestCase):
    def test_under_budget_rows_are_retryable(self) -> None:
        rows = [{"pr_number": 11, "branch": "feat/x"}]

        def lookup(pr_number: int) -> RetryAttemptLog:
            return RetryAttemptLog(pr_number=pr_number, attempts=1)

        retryable, escalated = partition_failed_prs_by_retry(
            rows, retry_lookup=lookup, policy=CIRetryPolicy(max_attempts=3)
        )
        self.assertEqual(len(retryable), 1)
        self.assertEqual(len(escalated), 0)
        self.assertEqual(retryable[0]["ci_retry_status"], "retryable")
        self.assertEqual(retryable[0]["ci_retry_attempts"], 1)
        self.assertEqual(retryable[0]["ci_retry_max"], 3)

    def test_at_or_over_budget_rows_are_escalated(self) -> None:
        rows = [
            {"pr_number": 11, "branch": "feat/a"},
            {"pr_number": 12, "branch": "feat/b"},
        ]

        def lookup(pr_number: int) -> RetryAttemptLog:
            return RetryAttemptLog(
                pr_number=pr_number, attempts=3 if pr_number == 12 else 0
            )

        retryable, escalated = partition_failed_prs_by_retry(
            rows, retry_lookup=lookup, policy=CIRetryPolicy(max_attempts=3)
        )
        self.assertEqual([r["pr_number"] for r in retryable], [11])
        self.assertEqual([r["pr_number"] for r in escalated], [12])
        self.assertEqual(escalated[0]["ci_retry_status"], "escalated")

    def test_lookup_returning_none_treated_as_empty_log(self) -> None:
        rows = [{"pr_number": 11}]

        def lookup(_pr_number: int):
            return None

        retryable, escalated = partition_failed_prs_by_retry(
            rows, retry_lookup=lookup
        )
        self.assertEqual(len(retryable), 1)
        self.assertEqual(len(escalated), 0)


class SelectNextTaskWithCIRetryGuardTests(unittest.TestCase):
    def test_retryable_pr_is_picked(self) -> None:
        github = _FakeGithubState(
            failed_prs=[{"pr_number": 11, "reason": "lint failed"}]
        )
        session = _FakeSessionState()
        candidate = select_next_task_with_ci_retry_guard(
            github_state=github,
            session_state=session,
            retry_lookup=lambda _pr: RetryAttemptLog(pr_number=11, attempts=1),
            policy=CIRetryPolicy(max_attempts=3),
        )
        self.assertEqual(candidate.source, SOURCE_CI_FAILED_PR)
        self.assertEqual(candidate.payload["pr_number"], 11)
        self.assertEqual(candidate.payload["ci_retry_attempts"], 1)

    def test_escalated_pr_is_skipped_falls_through_to_next_priority(self) -> None:
        github = _FakeGithubState(
            failed_prs=[{"pr_number": 11, "reason": "lint failed"}]
        )
        session = _FakeSessionState(
            approved=[{"session_id": "s1", "executor_role": "backend-engineer"}]
        )
        candidate = select_next_task_with_ci_retry_guard(
            github_state=github,
            session_state=session,
            retry_lookup=lambda _pr: RetryAttemptLog(
                pr_number=11, attempts=3
            ),
            policy=CIRetryPolicy(max_attempts=3),
        )
        # Failed PR exhausted budget → selector skips, falls through to
        # the approved coding job.
        self.assertEqual(candidate.source, SOURCE_APPROVED_CODING_JOB)
        # Escalated rows are surfaced on payload so the caller can log /
        # notify operator.
        self.assertIn("ci_retry_escalated", candidate.payload)
        self.assertEqual(len(candidate.payload["ci_retry_escalated"]), 1)
        self.assertEqual(
            candidate.payload["ci_retry_escalated"][0]["pr_number"], 11
        )

    def test_all_escalated_with_no_other_work_returns_idle(self) -> None:
        github = _FakeGithubState(
            failed_prs=[{"pr_number": 11}],
            orphan_issues=[],
        )
        session = _FakeSessionState()
        candidate = select_next_task_with_ci_retry_guard(
            github_state=github,
            session_state=session,
            retry_lookup=lambda _pr: RetryAttemptLog(
                pr_number=11, attempts=99
            ),
            policy=CIRetryPolicy(max_attempts=3),
        )
        self.assertEqual(candidate.source, SOURCE_IDLE)
        self.assertEqual(
            len(candidate.payload["ci_retry_escalated"]), 1
        )

    def test_no_failed_prs_behaves_like_plain_selector(self) -> None:
        github = _FakeGithubState(failed_prs=[])
        session = _FakeSessionState(
            approved=[{"session_id": "s1", "executor_role": "backend-engineer"}]
        )
        candidate = select_next_task_with_ci_retry_guard(
            github_state=github,
            session_state=session,
            retry_lookup=lambda _pr: RetryAttemptLog(pr_number=0),
        )
        self.assertEqual(candidate.source, SOURCE_APPROVED_CODING_JOB)


if __name__ == "__main__":
    unittest.main()
