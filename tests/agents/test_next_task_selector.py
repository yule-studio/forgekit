"""next_task_selector — Phase 2 of #73.

Pin the deterministic priority order:
  1. CI failed PR > 2. approved coding job > 3. unresolved discussion > 4. orphan issue > idle.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.next_task_selector import (
    SOURCE_APPROVED_CODING_JOB,
    SOURCE_CI_FAILED_PR,
    SOURCE_IDLE,
    SOURCE_ORPHAN_OPEN_ISSUE,
    SOURCE_UNRESOLVED_DISCUSSION,
    NextTaskCandidate,
    select_next_task,
    stamp_selection_to_session_extra,
)


class _FakeGithub:
    def __init__(
        self,
        *,
        failed_prs=(),
        orphan_issues=(),
    ) -> None:
        self.failed_prs = list(failed_prs)
        self.orphan_issues = list(orphan_issues)

    def list_failed_ci_active_prs(self):
        return self.failed_prs

    def list_open_issues_without_session(self):
        return self.orphan_issues


class _FakeSessions:
    def __init__(
        self,
        *,
        approved_jobs=(),
        unresolved=(),
    ) -> None:
        self.approved_jobs = list(approved_jobs)
        self.unresolved = list(unresolved)

    def list_approved_coding_jobs(self):
        return self.approved_jobs

    def list_unresolved_discussion_threads(self):
        return self.unresolved


class PriorityOrderTests(unittest.TestCase):
    def test_idle_when_all_sources_empty(self) -> None:
        result = select_next_task(
            github_state=_FakeGithub(),
            session_state=_FakeSessions(),
        )
        self.assertEqual(result.source, SOURCE_IDLE)
        self.assertGreater(result.priority, 4)

    def test_ci_failed_pr_wins_over_all(self) -> None:
        github = _FakeGithub(
            failed_prs=[{"pr_number": 70, "branch": "feature/x", "reason": "test"}],
            orphan_issues=[{"issue_number": 99, "title": "X"}],
        )
        sessions = _FakeSessions(
            approved_jobs=[{"session_id": "s1", "executor_role": "backend-engineer"}],
            unresolved=[{"thread_id": 5001, "session_id": "s2", "missing_roles": ["qa-engineer"]}],
        )
        result = select_next_task(github_state=github, session_state=sessions)
        self.assertEqual(result.source, SOURCE_CI_FAILED_PR)
        self.assertEqual(result.priority, 1)
        self.assertEqual(result.payload["pr_number"], 70)

    def test_approved_coding_job_wins_over_discussion_and_orphan(self) -> None:
        github = _FakeGithub(orphan_issues=[{"issue_number": 99, "title": "X"}])
        sessions = _FakeSessions(
            approved_jobs=[
                {"session_id": "s1", "executor_role": "backend-engineer"}
            ],
            unresolved=[{"thread_id": 5001, "session_id": "s2", "missing_roles": ["qa-engineer"]}],
        )
        result = select_next_task(github_state=github, session_state=sessions)
        self.assertEqual(result.source, SOURCE_APPROVED_CODING_JOB)
        self.assertEqual(result.priority, 2)
        self.assertEqual(result.payload["session_id"], "s1")

    def test_unresolved_discussion_wins_over_orphan(self) -> None:
        github = _FakeGithub(orphan_issues=[{"issue_number": 99, "title": "X"}])
        sessions = _FakeSessions(
            unresolved=[{"thread_id": 5001, "session_id": "s2", "missing_roles": ["qa-engineer"]}],
        )
        result = select_next_task(github_state=github, session_state=sessions)
        self.assertEqual(result.source, SOURCE_UNRESOLVED_DISCUSSION)
        self.assertEqual(result.priority, 3)

    def test_orphan_issue_when_only_source(self) -> None:
        github = _FakeGithub(orphan_issues=[{"issue_number": 99, "title": "X"}])
        sessions = _FakeSessions()
        result = select_next_task(github_state=github, session_state=sessions)
        self.assertEqual(result.source, SOURCE_ORPHAN_OPEN_ISSUE)
        self.assertEqual(result.priority, 4)
        self.assertEqual(result.payload["issue_number"], 99)


class StampToSessionExtraTests(unittest.TestCase):
    def test_stamp_writes_under_next_task_selection(self) -> None:
        candidate = NextTaskCandidate(
            source=SOURCE_APPROVED_CODING_JOB,
            priority=2,
            reason="...",
            payload={"session_id": "s1"},
            selected_at="2026-05-09T10:00:00+00:00",
        )
        new_extra = stamp_selection_to_session_extra({"foo": "bar"}, candidate, dispatched_at="2026-05-09T10:00:01+00:00")
        self.assertEqual(new_extra["foo"], "bar")
        self.assertIn("next_task_selection", new_extra)
        self.assertEqual(new_extra["next_task_selection"]["source"], SOURCE_APPROVED_CODING_JOB)
        self.assertEqual(new_extra["next_task_selection"]["dispatched_at"], "2026-05-09T10:00:01+00:00")

    def test_input_extra_unchanged(self) -> None:
        original = {"foo": "bar"}
        candidate = NextTaskCandidate(
            source=SOURCE_IDLE, priority=99, reason="", payload={}, selected_at=""
        )
        stamp_selection_to_session_extra(original, candidate)
        self.assertEqual(original, {"foo": "bar"})


if __name__ == "__main__":
    unittest.main()
