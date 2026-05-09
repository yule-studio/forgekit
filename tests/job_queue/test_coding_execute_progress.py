"""coding_execute_progress — Round 3 of #73.

Pin the surface that records a coding-executor outcome to:

  * ``session.extra['coding_execute_progress']`` history list (capped),
  * an ``obsidian_write`` queue row (kind=task-log → no approval gate),
  * an injected GitHub PR comment poster (best-effort, swallowed on
    failure so a GitHub blip never crashes the runtime).

The recorder must not perform any of those side-effects when the
collaborators are absent — a session with no obsidian writer + no
GitHub poster still gets the in-memory progress entry.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_execute_progress import (
    PROGRESS_STATUS_LABELS,
    SESSION_EXTRA_PROGRESS_KEY,
    TASK_LOG_NOTE_KIND,
    ProgressEntry,
    append_progress_history,
    build_progress_entry,
    make_github_pr_comment_fn,
    record_coding_execute_progress,
    render_progress_markdown,
    render_progress_summary_line,
    status_from_outcome,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteOutcome,
    CodingExecuteRequest,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.obsidian_writer_worker import (
    JOB_TYPE_OBSIDIAN_WRITE,
    ObsidianWriterWorker,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import Job, JobQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)


def _request(**overrides) -> CodingExecuteRequest:
    base = dict(
        session_id="sess-X",
        executor_role="backend-engineer",
        user_request="fix login",
        generated_prompt="(prompt)",
        write_scope=("services/auth/**",),
        forbidden_scope=(".github/workflows/**",),
        safety_rules=("no force push",),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-99-fix",
        repo_full_name="yule-studio/yule-studio-agent",
        issue_number=99,
        dry_run=True,
        metadata={},
    )
    base.update(overrides)
    return CodingExecuteRequest(**base)


def _outcome(
    *,
    terminal_state: str = JobState.SAVED.value,
    pr_number: int = 999,
    pr_url: str = "https://github.com/x/y/pull/999",
    commit_sha: str = "abc123def",
    branch: str = "agent/backend-engineer/issue-99-fix",
    test_summary=None,
    failure_reason=None,
) -> CodingExecuteOutcome:
    job = Job(
        job_id="job-1",
        session_id="sess-X",
        job_type="coding_execute",
        state=JobState.IN_PROGRESS,
        payload={
            "session_id": "sess-X",
            "executor_role": "backend-engineer",
        },
    )
    return CodingExecuteOutcome(
        job=job,
        terminal_state=terminal_state,
        branch=branch,
        commit_sha=commit_sha,
        pr_number=pr_number,
        pr_url=pr_url,
        test_summary=test_summary or {"status": "ok"},
        failure_reason=failure_reason,
    )


# ---------------------------------------------------------------------------
# status_from_outcome
# ---------------------------------------------------------------------------


class StatusFromOutcomeTests(unittest.TestCase):
    def test_saved_to_done(self) -> None:
        self.assertEqual(status_from_outcome(_outcome()), "done")

    def test_failed_retryable_to_retry_ready(self) -> None:
        self.assertEqual(
            status_from_outcome(
                _outcome(
                    terminal_state=JobState.FAILED_RETRYABLE.value,
                    failure_reason="test_failed",
                )
            ),
            "retry_ready",
        )

    def test_failed_terminal_to_blocked(self) -> None:
        self.assertEqual(
            status_from_outcome(
                _outcome(
                    terminal_state=JobState.FAILED_TERMINAL.value,
                    failure_reason="protected_branch_blocked",
                )
            ),
            "blocked",
        )


# ---------------------------------------------------------------------------
# render_progress_markdown
# ---------------------------------------------------------------------------


class RenderMarkdownTests(unittest.TestCase):
    def test_includes_pr_link_when_url_present(self) -> None:
        entry = build_progress_entry(_outcome(), request=_request())
        body = render_progress_markdown(entry)
        self.assertIn("PR: [#999](https://github.com/x/y/pull/999)", body)
        self.assertIn("backend-engineer", body)
        self.assertIn("agent/backend-engineer/issue-99-fix", body)

    def test_omits_pr_link_when_dry_run(self) -> None:
        entry = build_progress_entry(
            _outcome(pr_number=None, pr_url="", commit_sha=""),
            request=_request(dry_run=True),
        )
        body = render_progress_markdown(entry)
        self.assertNotIn("PR:", body)


# ---------------------------------------------------------------------------
# Round 4: per-status operator hints + summary line for the status surface
# ---------------------------------------------------------------------------


class StatusHintsTests(unittest.TestCase):
    def test_done_includes_completion_label(self) -> None:
        entry = build_progress_entry(_outcome(), request=_request())
        body = render_progress_markdown(entry)
        self.assertIn("✅", body)
        self.assertIn("완료", body)
        self.assertIn("`done`", body)
        # Operator hint mentions producer follow-up.
        self.assertIn("autonomy producer", body)

    def test_blocked_emits_operator_review_hint(self) -> None:
        entry = build_progress_entry(
            _outcome(
                terminal_state=JobState.FAILED_TERMINAL.value,
                failure_reason="protected_branch_blocked",
            ),
            request=_request(),
        )
        body = render_progress_markdown(entry)
        self.assertIn("⛔", body)
        self.assertIn("차단됨", body)
        # Operator hint must spell out "no auto-retry".
        self.assertIn("재시도하지 않습니다", body)
        self.assertIn("protected_branch_blocked", body)

    def test_retry_ready_emits_transient_hint(self) -> None:
        entry = build_progress_entry(
            _outcome(
                terminal_state=JobState.FAILED_RETRYABLE.value,
                failure_reason="test_failed",
            ),
            request=_request(),
        )
        body = render_progress_markdown(entry)
        self.assertIn("🔁", body)
        self.assertIn("재시도 대기", body)
        self.assertIn("transient", body)

    def test_needs_approval_uses_explicit_completion_status_override(self) -> None:
        entry = build_progress_entry(
            _outcome(
                terminal_state=JobState.SAVED.value,
            ),
            request=_request(),
            completion_status="needs_approval",
        )
        body = render_progress_markdown(entry)
        self.assertIn("🙋", body)
        self.assertIn("승인 대기", body)
        self.assertIn("승인-대기", body)

    def test_locked_status_renders_lock_icon_and_hint(self) -> None:
        entry = build_progress_entry(
            _outcome(),
            request=_request(),
            completion_status="locked",
        )
        body = render_progress_markdown(entry)
        self.assertIn("🔒", body)
        self.assertIn("점유 중", body)
        self.assertIn("lock", body)

    def test_unknown_status_falls_back_to_blocked_label(self) -> None:
        entry = build_progress_entry(
            _outcome(),
            request=_request(),
            completion_status="something-weird",
        )
        body = render_progress_markdown(entry)
        # Falls back to blocked palette without crashing.
        self.assertIn("⛔", body)
        self.assertIn("차단됨", body)

    def test_summary_line_shape(self) -> None:
        entry = build_progress_entry(
            _outcome(),
            request=_request(),
            completion_status="needs_approval",
        )
        line = render_progress_summary_line(entry)
        self.assertIn("🙋", line)
        self.assertIn("backend-engineer", line)
        self.assertIn("sess-X", line)
        self.assertIn("pr=#999", line)

    def test_label_table_carries_all_four_states_plus_locked(self) -> None:
        for key in ("done", "retry_ready", "needs_approval", "blocked", "locked"):
            self.assertIn(key, PROGRESS_STATUS_LABELS)
            label = PROGRESS_STATUS_LABELS[key]
            self.assertIn("icon", label)
            self.assertIn("headline", label)
            self.assertIn("operator_hint", label)


# ---------------------------------------------------------------------------
# append_progress_history bounds
# ---------------------------------------------------------------------------


class AppendHistoryTests(unittest.TestCase):
    def test_bounds_at_50_entries(self) -> None:
        existing = [
            {"session_id": "sess", "completion_status": "done", "at": str(i)}
            for i in range(60)
        ]
        extra = {SESSION_EXTRA_PROGRESS_KEY: existing}
        entry = build_progress_entry(_outcome(), request=_request())
        new_extra = append_progress_history(extra, entry)
        history = new_extra[SESSION_EXTRA_PROGRESS_KEY]
        self.assertEqual(len(history), 50)
        # Newest entry is the one we appended.
        self.assertEqual(history[-1]["session_id"], "sess-X")


# ---------------------------------------------------------------------------
# record_coding_execute_progress — full integration with a real
# ObsidianWriterWorker + fake comment poster
# ---------------------------------------------------------------------------


class _SessionStore:
    def __init__(self) -> None:
        self.sessions: dict = {}

    def update(self, session, *, now):
        self.sessions[session.session_id] = session
        return session


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        # ObsidianWriterWorker needs render/write fns even when only
        # used as an enqueue surface — pass no-op stubs.
        self.writer = ObsidianWriterWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            render_fn=lambda req: type("Note", (), {"frontmatter": {}, "body": ""})(),
            write_fn=lambda note, path, req: None,
            vault_root_resolver=lambda req: None,
        )
        self.store = _SessionStore()


class RecordIntegrationTests(_Fixture):
    def test_full_pipeline_enqueues_obsidian_and_posts_pr_comment(self) -> None:
        comments: list = []

        def post_fn(*, repo, pr_number, body):
            comments.append({"repo": repo, "pr_number": pr_number, "body": body})

        session = _FakeSession(session_id="sess-X")
        result = record_coding_execute_progress(
            session=session,
            outcome=_outcome(),
            request=_request(),
            obsidian_writer=self.writer,
            github_comment_fn=post_fn,
            update_session_fn=self.store.update,
            repo_full_name="yule-studio/yule-studio-agent",
        )
        self.assertEqual(result.entry.completion_status, "done")
        self.assertIsNotNone(result.obsidian_job_id)
        self.assertTrue(result.github_comment_posted)
        # Obsidian queue row exists, kind=task-log.
        rows = [
            r
            for r in self.queue.list_for_session("sess-X")
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].payload["note_kind"], TASK_LOG_NOTE_KIND)
        # task-log does NOT need approval — payload approval_id is empty.
        self.assertEqual(rows[0].payload.get("approval_id"), None)
        # PR comment fired with rendered markdown.
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["pr_number"], 999)
        self.assertIn("coding-executor", comments[0]["body"])
        # Session persisted with progress entry.
        self.assertTrue(result.session_persisted)
        history = self.store.sessions["sess-X"].extra[SESSION_EXTRA_PROGRESS_KEY]
        self.assertEqual(len(history), 1)

    def test_no_collaborators_still_records_history(self) -> None:
        session = _FakeSession(session_id="sess-X")
        result = record_coding_execute_progress(
            session=session,
            outcome=_outcome(),
            request=_request(),
            obsidian_writer=None,
            github_comment_fn=None,
            update_session_fn=self.store.update,
        )
        self.assertEqual(result.obsidian_skipped_reason, "no_obsidian_writer")
        self.assertFalse(result.github_comment_posted)
        # Progress entry STILL ends up on the session.
        self.assertTrue(result.session_persisted)

    def test_github_failure_is_swallowed(self) -> None:
        def boom(**_kwargs):
            raise RuntimeError("github 502")

        session = _FakeSession(session_id="sess-X")
        result = record_coding_execute_progress(
            session=session,
            outcome=_outcome(),
            request=_request(),
            obsidian_writer=self.writer,
            github_comment_fn=boom,
            update_session_fn=self.store.update,
        )
        self.assertFalse(result.github_comment_posted)
        self.assertIn("502", result.github_comment_error or "")
        # Obsidian write still happened.
        rows = [
            r
            for r in self.queue.list_for_session("sess-X")
            if r.job_type == JOB_TYPE_OBSIDIAN_WRITE
        ]
        self.assertEqual(len(rows), 1)

    def test_dry_run_skips_pr_comment(self) -> None:
        comments: list = []

        def post_fn(*, repo, pr_number, body):
            comments.append((repo, pr_number, body))

        session = _FakeSession(session_id="sess-X")
        # dry-run outcome — no PR was opened.
        outcome = _outcome(
            pr_number=None, pr_url="", commit_sha="", test_summary={"dry_run": True}
        )
        result = record_coding_execute_progress(
            session=session,
            outcome=outcome,
            request=_request(dry_run=True),
            obsidian_writer=self.writer,
            github_comment_fn=post_fn,
            update_session_fn=self.store.update,
        )
        self.assertFalse(result.github_comment_posted)
        self.assertEqual(result.github_comment_error, "no_pr")
        self.assertEqual(comments, [])


# ---------------------------------------------------------------------------
# make_github_pr_comment_fn
# ---------------------------------------------------------------------------


class MakeGithubCommentFnTests(unittest.TestCase):
    def test_routes_to_create_issue_comment(self) -> None:
        calls: list = []

        class _FakeLive:
            def create_issue_comment(self, *, repo, issue_number, body):
                calls.append({"repo": repo, "issue_number": issue_number, "body": body})
                return {"id": 1}

        post_fn = make_github_pr_comment_fn(_FakeLive())
        post_fn(repo="o/r", pr_number=42, body="hello")
        self.assertEqual(calls, [{"repo": "o/r", "issue_number": 42, "body": "hello"}])


if __name__ == "__main__":
    unittest.main()
