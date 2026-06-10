"""coding_execute_dispatcher — Round 3 of #73.

Pin the production producer that turns ``session.extra['coding_job']``
``status="ready"`` into a queued ``coding_execute`` row:

  * iter_ready_coding_jobs filters statuses + already-dispatched rows.
  * build_coding_execute_request maps the persisted dict cleanly.
  * dispatch_ready_coding_jobs is idempotent against the worker's
    own dedup AND against repeated session scans (writes a marker
    so the next iteration skips).
  * WorkflowSessionState surfaces the same data to the next-task
    selector.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    WorkflowSessionState,
    build_coding_execute_request,
    dispatch_ready_coding_jobs,
    iter_ready_coding_jobs,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Fakes — minimal session shape the dispatcher reads
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    """Minimal mutable stand-in for WorkflowSession.

    The dispatcher reads ``extra`` and writes back via ``replace``;
    ``thread_id`` / ``channel_id`` are surfaced by
    :class:`WorkflowSessionState` for the selector.
    """

    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    thread_id: int = 0
    channel_id: int = 0


def _coding_job(
    *,
    session_id: str = "sess-A",
    executor_role: str = "backend-engineer",
    status: str = "ready",
    issue_number: int = 99,
    repo_full_name: str = "yule-studio/yule-studio-agent",
    base_branch: str = "main",
    branch_hint: str = "agent/backend-engineer/issue-99-fix",
    user_request: str = "users 401 회복",
    write_scope=("services/auth/**",),
    forbidden_scope=(".github/workflows/**",),
    safety_rules=("no force push",),
    generated_prompt: str = "(prompt)",
    extra_metadata: Mapping[str, Any] = None,
) -> Mapping[str, Any]:
    """Build a persisted coding_job dict the way the gateway writes it."""

    metadata = {
        "repo_full_name": repo_full_name,
        "base_branch": base_branch,
        "issue_number": issue_number,
        "branch_hint": branch_hint,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "session_id": session_id,
        "user_request": user_request,
        "executor_role": executor_role,
        "review_roles": ["tech-lead", "qa-engineer"],
        "participant_roles": [executor_role, "tech-lead", "qa-engineer"],
        "write_scope": list(write_scope),
        "forbidden_scope": list(forbidden_scope),
        "safety_rules": list(safety_rules),
        "reason": "deterministic test fixture",
        "status": status,
        "generated_prompt": generated_prompt,
        "created_at": "2026-05-08T00:00:00+00:00",
        "approved_at": "2026-05-08T01:00:00+00:00" if status == "ready" else None,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# iter_ready_coding_jobs
# ---------------------------------------------------------------------------


class IterReadyCodingJobsTests(unittest.TestCase):
    def test_yields_only_ready_status(self) -> None:
        ready = _FakeSession(
            session_id="ready-1",
            extra={"coding_job": _coding_job(session_id="ready-1")},
        )
        pending = _FakeSession(
            session_id="pending-1",
            extra={
                "coding_job": _coding_job(session_id="pending-1", status="pending_approval")
            },
        )
        completed = _FakeSession(
            session_id="completed-1",
            extra={
                "coding_job": _coding_job(session_id="completed-1", status="completed")
            },
        )
        out = list(iter_ready_coding_jobs(session_loader=lambda: [ready, pending, completed]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].session_id, "ready-1")

    def test_skips_already_dispatched(self) -> None:
        already = _FakeSession(
            session_id="already-1",
            extra={
                "coding_job": _coding_job(session_id="already-1"),
                SESSION_EXTRA_DISPATCH_KEY: {
                    "job_id": "queued-job-xyz",
                    "executor_role": "backend-engineer",
                    "dispatched_at": "2026-05-08T01:00:00+00:00",
                },
            },
        )
        out = list(iter_ready_coding_jobs(session_loader=lambda: [already]))
        self.assertEqual(out, [])

    def test_includes_dispatched_when_flag_set(self) -> None:
        already = _FakeSession(
            session_id="already-1",
            extra={
                "coding_job": _coding_job(session_id="already-1"),
                SESSION_EXTRA_DISPATCH_KEY: {"job_id": "queued"},
            },
        )
        out = list(
            iter_ready_coding_jobs(
                session_loader=lambda: [already], include_dispatched=True
            )
        )
        self.assertEqual(len(out), 1)

    def test_skips_session_without_executor_role(self) -> None:
        bad = _FakeSession(
            session_id="bad",
            extra={"coding_job": _coding_job(session_id="bad", executor_role="")},
        )
        out = list(iter_ready_coding_jobs(session_loader=lambda: [bad]))
        self.assertEqual(out, [])

    def test_loader_failure_is_swallowed(self) -> None:
        def loader():
            raise RuntimeError("cache exploded")
        # Must not raise.
        out = list(iter_ready_coding_jobs(session_loader=loader))
        self.assertEqual(out, [])

    def test_missing_coding_job_extra_is_skipped(self) -> None:
        s = _FakeSession(session_id="x", extra={"unrelated": "value"})
        out = list(iter_ready_coding_jobs(session_loader=lambda: [s]))
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# build_coding_execute_request
# ---------------------------------------------------------------------------


class BuildRequestTests(unittest.TestCase):
    def test_metadata_repo_wins_over_env(self) -> None:
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(
                        session_id="A",
                        extra={"coding_job": _coding_job(session_id="A", repo_full_name="meta/repo")},
                    )
                ]
            )
        )
        req = build_coding_execute_request(ready, env={"YULE_CODING_EXECUTOR_REPO": "env/repo"})
        self.assertEqual(req.repo_full_name, "meta/repo")

    def test_falls_back_to_env_repo(self) -> None:
        job = _coding_job(session_id="A", repo_full_name="")
        # remove repo from metadata
        job["metadata"].pop("repo_full_name", None)
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(session_id="A", extra={"coding_job": job})
                ]
            )
        )
        req = build_coding_execute_request(ready, env={"YULE_CODING_EXECUTOR_REPO": "env/repo"})
        self.assertEqual(req.repo_full_name, "env/repo")

    def test_dry_run_defaults_true(self) -> None:
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(
                        session_id="A",
                        extra={"coding_job": _coding_job(session_id="A")},
                    )
                ]
            )
        )
        req = build_coding_execute_request(ready, env={})
        self.assertTrue(req.dry_run)

    def test_env_can_force_live(self) -> None:
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(
                        session_id="A",
                        extra={"coding_job": _coding_job(session_id="A")},
                    )
                ]
            )
        )
        req = build_coding_execute_request(
            ready, env={"YULE_CODING_EXECUTOR_DRY_RUN": "false"}
        )
        self.assertFalse(req.dry_run)

    def test_metadata_can_opt_out_of_dry_run(self) -> None:
        job = _coding_job(session_id="A", extra_metadata={"dry_run": False})
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(session_id="A", extra={"coding_job": job})
                ]
            )
        )
        req = build_coding_execute_request(ready, env={})
        self.assertFalse(req.dry_run)

    def test_env_forces_dry_run_back_on(self) -> None:
        # Operator emergency switch: even if metadata says go live, an
        # env-set dry_run still flips it back.
        job = _coding_job(session_id="A", extra_metadata={"dry_run": False})
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(session_id="A", extra={"coding_job": job})
                ]
            )
        )
        req = build_coding_execute_request(
            ready, env={"YULE_CODING_EXECUTOR_DRY_RUN": "1"}
        )
        self.assertTrue(req.dry_run)

    def test_carries_write_and_forbidden_scope_intact(self) -> None:
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(
                        session_id="A",
                        extra={"coding_job": _coding_job(session_id="A")},
                    )
                ]
            )
        )
        req = build_coding_execute_request(ready, env={})
        self.assertIn("services/auth/**", req.write_scope)
        self.assertIn(".github/workflows/**", req.forbidden_scope)
        # Hard rail: forbidden_scope must NOT silently drop entries the
        # gateway approved.
        self.assertEqual(req.safety_rules, ("no force push",))

    def test_branch_hint_carries_through(self) -> None:
        ready = next(
            iter_ready_coding_jobs(
                session_loader=lambda: [
                    _FakeSession(
                        session_id="A",
                        extra={
                            "coding_job": _coding_job(
                                session_id="A",
                                branch_hint="agent/backend-engineer/issue-99-fix",
                            )
                        },
                    )
                ]
            )
        )
        req = build_coding_execute_request(ready, env={})
        self.assertEqual(req.branch_hint, "agent/backend-engineer/issue-99-fix")


# ---------------------------------------------------------------------------
# dispatch_ready_coding_jobs — full producer cycle against a real queue
# ---------------------------------------------------------------------------


class DispatchProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db_path)
        self.heartbeats = HeartbeatStore(db_path=db_path)
        self.worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )
        # In-memory session "store" — dispatcher's update_session_fn
        # writes back here so test assertions can read the marker.
        self.sessions: list[_FakeSession] = []

        def _loader() -> Sequence[Any]:
            return list(self.sessions)

        def _update_session(session: Any, *, now: datetime) -> Any:
            for idx, existing in enumerate(self.sessions):
                if existing.session_id == session.session_id:
                    self.sessions[idx] = session
                    return session
            self.sessions.append(session)
            return session

        self.loader = _loader
        self.update_session_fn = _update_session

    def test_single_session_produces_one_queue_row(self) -> None:
        self.sessions.append(
            _FakeSession(
                session_id="A",
                extra={"coding_job": _coding_job(session_id="A")},
            )
        )
        out = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=self.loader,
            update_session_fn=self.update_session_fn,
            env={},
        )
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].created)
        self.assertIsNotNone(out[0].job_id)
        # Worker queue row exists.
        rows = self.queue.list_for_session("A")
        coding = [r for r in rows if r.job_type == JOB_TYPE_CODING_EXECUTE]
        self.assertEqual(len(coding), 1)
        self.assertEqual(coding[0].state, JobState.QUEUED)
        # Marker stamped.
        marker = self.sessions[0].extra.get(SESSION_EXTRA_DISPATCH_KEY)
        self.assertIsNotNone(marker)
        self.assertEqual(marker["job_id"], out[0].job_id)

    def test_second_dispatch_is_idempotent(self) -> None:
        self.sessions.append(
            _FakeSession(
                session_id="A",
                extra={"coding_job": _coding_job(session_id="A")},
            )
        )
        first = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=self.loader,
            update_session_fn=self.update_session_fn,
            env={},
        )
        self.assertEqual(len(first), 1)
        # Second pass — marker present, dispatcher skips entirely.
        second = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=self.loader,
            update_session_fn=self.update_session_fn,
            env={},
        )
        self.assertEqual(second, ())
        # Queue stays at 1 row.
        rows = [r for r in self.queue.list_for_session("A") if r.job_type == JOB_TYPE_CODING_EXECUTE]
        self.assertEqual(len(rows), 1)

    def test_multiple_sessions_each_produce_one_row(self) -> None:
        for sid in ("A", "B", "C"):
            self.sessions.append(
                _FakeSession(
                    session_id=sid,
                    extra={"coding_job": _coding_job(session_id=sid)},
                )
            )
        out = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=self.loader,
            update_session_fn=self.update_session_fn,
            env={},
        )
        self.assertEqual(len(out), 3)
        self.assertEqual({d.session_id for d in out}, {"A", "B", "C"})

    def test_pending_approval_session_ignored(self) -> None:
        self.sessions.append(
            _FakeSession(
                session_id="A",
                extra={
                    "coding_job": _coding_job(
                        session_id="A", status="pending_approval"
                    )
                },
            )
        )
        out = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=self.loader,
            update_session_fn=self.update_session_fn,
            env={},
        )
        self.assertEqual(out, ())

    def test_enqueue_failure_does_not_persist_marker(self) -> None:
        self.sessions.append(
            _FakeSession(
                session_id="A",
                extra={"coding_job": _coding_job(session_id="A")},
            )
        )

        def _bad_enqueue(*args, **kwargs):
            raise RuntimeError("queue write failed")

        # Surgical patch: monkey-patch worker.enqueue for this test only.
        original = self.worker.enqueue
        self.worker.enqueue = _bad_enqueue  # type: ignore[assignment]
        try:
            out = dispatch_ready_coding_jobs(
                worker=self.worker,
                session_loader=self.loader,
                update_session_fn=self.update_session_fn,
                env={},
            )
        finally:
            self.worker.enqueue = original  # type: ignore[assignment]

        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].job_id)
        self.assertIn("enqueue failed", out[0].error or "")
        # Marker NOT persisted — next tick will retry.
        self.assertNotIn(
            SESSION_EXTRA_DISPATCH_KEY, self.sessions[0].extra
        )


# ---------------------------------------------------------------------------
# WorkflowSessionState — selector adapter
# ---------------------------------------------------------------------------


class WorkflowSessionStateTests(unittest.TestCase):
    def test_lists_only_undispatched_by_default(self) -> None:
        sessions = [
            _FakeSession(
                session_id="ready-1",
                extra={"coding_job": _coding_job(session_id="ready-1")},
            ),
            _FakeSession(
                session_id="ready-2",
                extra={
                    "coding_job": _coding_job(session_id="ready-2"),
                    SESSION_EXTRA_DISPATCH_KEY: {"job_id": "in-flight"},
                },
            ),
            _FakeSession(
                session_id="pending",
                extra={"coding_job": _coding_job(session_id="pending", status="pending_approval")},
            ),
        ]
        state = WorkflowSessionState(session_loader=lambda: sessions)
        rows = list(state.list_approved_coding_jobs())
        self.assertEqual([r["session_id"] for r in rows], ["ready-1"])
        self.assertEqual(rows[0]["executor_role"], "backend-engineer")

    def test_includes_dispatched_when_opt_in(self) -> None:
        sessions = [
            _FakeSession(
                session_id="ready-1",
                extra={"coding_job": _coding_job(session_id="ready-1")},
            ),
            _FakeSession(
                session_id="ready-2",
                extra={
                    "coding_job": _coding_job(session_id="ready-2"),
                    SESSION_EXTRA_DISPATCH_KEY: {"job_id": "in-flight"},
                },
            ),
        ]
        state = WorkflowSessionState(
            session_loader=lambda: sessions, include_dispatched=True
        )
        rows = list(state.list_approved_coding_jobs())
        self.assertEqual(len(rows), 2)
        # In-flight row carries the dispatch marker so the operator
        # surface can show it.
        in_flight = next(r for r in rows if r["session_id"] == "ready-2")
        self.assertEqual(in_flight["dispatch"]["job_id"], "in-flight")

    def test_no_discussion_loader_returns_empty_tuple(self) -> None:
        state = WorkflowSessionState()
        self.assertEqual(state.list_unresolved_discussion_threads(), ())

    def test_discussion_loader_filters_non_mappings(self) -> None:
        state = WorkflowSessionState(
            discussion_loader=lambda: [
                {"session_id": "A", "thread_id": 11},
                "junk",
                {"session_id": "B"},
            ]
        )
        rows = list(state.list_unresolved_discussion_threads())
        self.assertEqual([r["session_id"] for r in rows], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
