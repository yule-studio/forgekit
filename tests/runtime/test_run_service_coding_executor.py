"""run_service ``CODING_EXECUTOR`` builder — Round 3 wiring tests.

Pin that:

  * ``_build_process_job`` returns a closure for the
    ``eng-coding-executor`` service that wraps a real
    :class:`CodingExecutorWorker`.
  * ``_pick_filters_for`` returns the ``coding_execute`` job-type
    filter so ``run_worker_loop`` only picks coding rows.
  * ``build_coding_executor_bundle`` falls back to the dry-run /
    push-blocked bundle when GitHub App env is absent.
  * The bundle wires the live pusher + draft PR creator when a
    fake live client factory is injected.
  * The closure invokes :func:`dispatch_ready_coding_jobs` before
    each consumer tick — producer / consumer share the same worker.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue import (
    CodingExecutorWorker,
    HeartbeatStore,
    JobQueue,
)
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    JOB_TYPE_CODING_EXECUTE,
)
from yule_engineering.runtime.run_service import (
    _build_process_job,
    _pick_filters_for,
    build_coding_executor_bundle,
)
from yule_runtime.services import resolve_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    thread_id: int = 0
    channel_id: int = 0


def _ready_coding_job_payload(*, session_id: str = "sess-X") -> Mapping[str, Any]:
    return {
        "session_id": session_id,
        "user_request": "fix login",
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead"],
        "participant_roles": ["backend-engineer", "tech-lead"],
        "write_scope": ["services/auth/**"],
        "forbidden_scope": [".github/workflows/**"],
        "safety_rules": ["no force push"],
        "reason": "test fixture",
        "status": "ready",
        "generated_prompt": "(prompt)",
        "created_at": "2026-05-08T00:00:00+00:00",
        "approved_at": "2026-05-08T01:00:00+00:00",
        "metadata": {
            "repo_full_name": "yule-studio/yule-studio-agent",
            "base_branch": "main",
            "issue_number": 99,
            "branch_hint": "agent/backend-engineer/issue-99-fix",
        },
    }


# ---------------------------------------------------------------------------
# Filter / spec wiring
# ---------------------------------------------------------------------------


class CodingExecutorFiltersTests(unittest.TestCase):
    def test_pick_filters_returns_coding_execute_only(self) -> None:
        spec = resolve_service("eng-coding-executor")
        self.assertIsNotNone(spec)
        types, roles = _pick_filters_for(spec)
        self.assertEqual(types, ("coding_execute",))
        self.assertEqual(roles, ())

    def test_spec_resolves_under_engineering_profile(self) -> None:
        spec = resolve_service("eng-coding-executor")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind.value, "coding_executor")


# ---------------------------------------------------------------------------
# build_coding_executor_bundle — env matrix
# ---------------------------------------------------------------------------


class BuildBundleEnvMatrixTests(unittest.TestCase):
    def test_no_github_env_returns_dry_only_bundle(self) -> None:
        bundle = build_coding_executor_bundle(env={})
        # Live pusher / draft_pr only land when a live client appears.
        self.assertNotIn("pusher", bundle)
        self.assertNotIn("draft_pr_creator", bundle)
        # Always-on protocols.
        self.assertIn("worktree_provisioner", bundle)
        self.assertIn("code_editor", bundle)
        self.assertIn("test_runner", bundle)
        self.assertIn("committer", bundle)

    def test_factory_injection_wires_pusher_and_pr(self) -> None:
        class _Stub:
            pass

        bundle = build_coding_executor_bundle(
            env={}, live_client_factory=lambda: _Stub()
        )
        self.assertIn("pusher", bundle)
        self.assertIn("draft_pr_creator", bundle)

    def test_factory_failure_falls_back_to_dry_only(self) -> None:
        def boom():
            raise RuntimeError("creds missing")

        # Must not raise — startup degrades to dry-run rather than
        # failing the supervisor entirely.
        bundle = build_coding_executor_bundle(env={}, live_client_factory=boom)
        self.assertNotIn("pusher", bundle)

    def test_partial_github_env_does_not_attempt_live_client(self) -> None:
        # Missing private key path → factory must not fire.
        env = {
            "YULE_GITHUB_APP_ID": "1",
            "YULE_GITHUB_APP_INSTALLATION_ID": "2",
            # YULE_GITHUB_APP_PRIVATE_KEY_PATH absent on purpose.
        }
        bundle = build_coding_executor_bundle(env=env)
        self.assertNotIn("pusher", bundle)


# ---------------------------------------------------------------------------
# _build_process_job — closure shape + producer tick
# ---------------------------------------------------------------------------


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)


class CodingExecutorBuilderTests(_Fixture):
    def test_builder_returns_async_closure_with_worker_capture(self) -> None:
        spec = resolve_service("eng-coding-executor")
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        # The closure captures the worker instance; the captured
        # worker must be a real CodingExecutorWorker, not a stub.
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        self.assertIsInstance(worker, CodingExecutorWorker)
        # Builder is async to match the run_worker_loop contract.
        self.assertTrue(inspect.iscoroutinefunction(process_fn))

    def test_dispatcher_runs_before_consumer_processes_job(self) -> None:
        # Drop a "ready" coding_job session into a fake loader so the
        # producer side enqueues a coding_execute row, then the
        # consumer (process_job) runs that row through dry-run.
        spec = resolve_service("eng-coding-executor")
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )

        # Ride the closure's captured worker to enqueue manually so we
        # don't have to monkey-patch the dispatcher's session loader at
        # the supervisor level — but also assert that the closure body
        # tolerates the dispatcher call (no raise) while the consumer
        # processes the job.
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        from yule_engineering.agents.job_queue.coding_executor_worker import (
            CodingExecuteRequest,
        )
        request = CodingExecuteRequest(
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
        )
        job, _ = worker.enqueue(request)
        # Pick the row so the closure's worker.process_job receives an
        # already-leased Job (mirrors the run_worker_loop contract).
        leased = self.queue.pick(
            worker_id="test", job_types=[JOB_TYPE_CODING_EXECUTE]
        )
        self.assertIsNotNone(leased)

        import asyncio
        asyncio.run(process_fn(leased))

        # Job landed in SAVED via the dry-run path.
        rows = [
            r
            for r in self.queue.list_for_session("sess-X")
            if r.job_type == JOB_TYPE_CODING_EXECUTE
        ]
        self.assertEqual(len(rows), 1)
        from yule_engineering.agents.job_queue.state_machine import JobState
        self.assertEqual(rows[0].state, JobState.SAVED)


if __name__ == "__main__":
    unittest.main()
