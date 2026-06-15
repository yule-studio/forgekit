"""Self-improvement proposal → coding-executor queue handoff (WT2 live loop).

Pins the seam closed by WT2: a supervisor self-improvement tick that detects a
delegable problem must produce a real ``coding_execute`` job on the queue (draft-
PR only), not just a journal entry. Also pins the safety refusal (no
``draft_pr_only`` → no enqueue) and the env opt-in flag.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.lifecycle.runtime_self_improvement_loop import (
    SelfImprovementDispatcher,
)
from yule_engineering.agents.lifecycle.problem_ledger import ProblemLedger
from yule_engineering.agents.lifecycle.delegated_operator import DelegatedRateLedger
from yule_engineering.agents.lifecycle.self_improvement_worktree import (
    InMemoryWorktreeRegistry,
)
from yule_engineering.agents.lifecycle.self_improvement_seed_detectors import (
    ObservationContext,
)
from yule_engineering.agents.lifecycle.runtime_self_improvement_wiring import (
    build_executor_handoff_hook,
    build_queue_executor_enqueue_fn,
    enqueue_enabled,
)


class _RecordingProvisioner:
    def __init__(self) -> None:
        self.created: list = []
        self._existing: set = set()

    def create(self, *, branch: str, path: str, base_branch: str, cwd: str) -> None:
        self.created.append((branch, path))
        self._existing.add((branch, path))

    def exists(self, *, branch: str, path: str) -> bool:
        return (branch, path) in self._existing

    def remove(self, *, branch: str, path: str, force: bool = False) -> None:
        self._existing.discard((branch, path))


def _job(*, job_type: str, payload: Mapping[str, Any], result: Mapping[str, Any]) -> Any:
    return SimpleNamespace(
        job_type=job_type,
        state=SimpleNamespace(value="saved"),
        payload=payload,
        result=result,
        job_id="j",
    )


def _observation(jobs: Sequence[Any]) -> ObservationContext:
    return ObservationContext(
        jobs=jobs,
        failed_jobs=jobs,
        sessions=(),
        heartbeats={},
        now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )


class QueueEnqueueFnTests(unittest.TestCase):
    def _queue(self) -> JobQueue:
        return JobQueue(db_path=Path(tempfile.mkdtemp()) / "q.db")

    def test_draft_payload_enqueues_coding_execute(self) -> None:
        q = self._queue()
        fn = build_queue_executor_enqueue_fn(job_queue=q)
        job_id = fn(payload={
            "problem_signature": "sig-1",
            "draft_pr_only": True,
            "owner_role": "backend-engineer",
        })
        self.assertIsNotNone(job_id)
        # the job is on the queue as a coding_execute job
        from yule_engineering.agents.job_queue.coding_executor_reason import (
            JOB_TYPE_CODING_EXECUTE,
        )
        rows = q.list_for_session("self-improvement:sig-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].job_type, JOB_TYPE_CODING_EXECUTE)
        self.assertEqual(rows[0].payload.get("owner_role"), "backend-engineer")

    def test_non_draft_payload_refused(self) -> None:
        q = self._queue()
        fn = build_queue_executor_enqueue_fn(job_queue=q)
        self.assertIsNone(fn(payload={"problem_signature": "sig-2", "draft_pr_only": False}))
        self.assertEqual(len(q.list_for_session("self-improvement:sig-2")), 0)

    def test_enqueue_flag_opt_in(self) -> None:
        self.assertFalse(enqueue_enabled({}))
        self.assertTrue(enqueue_enabled({"YULE_SELF_IMPROVEMENT_ENQUEUE_ENABLED": "1"}))


class TickToQueueIntegrationTests(unittest.TestCase):
    def test_detected_problem_lands_coding_execute_job(self) -> None:
        q = JobQueue(db_path=Path(tempfile.mkdtemp()) / "q.db")
        enqueue_fn = build_queue_executor_enqueue_fn(job_queue=q)
        handoff = build_executor_handoff_hook(enqueue_fn=enqueue_fn)

        observation = _observation(
            jobs=(
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "no_matching_approval"},
                ),
            )
        )
        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ProblemLedger(),
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            worktree_provisioner=_RecordingProvisioner(),
            executor_handoff_hook=handoff,
        )

        report = dispatcher.run_tick()
        self.assertGreaterEqual(len(report.handled), 1)
        # a real coding_execute job is now on the queue, carrying the draft rail
        from yule_engineering.agents.job_queue.coding_executor_reason import (
            JOB_TYPE_CODING_EXECUTE,
        )
        delegated = [o for o in report.handled if o.executor_handoff_job_id]
        self.assertTrue(delegated, "expected at least one delegated handoff")
        job_id = delegated[0].executor_handoff_job_id
        all_rows = [
            r for sid in {f"self-improvement:{o.problem.signature}" for o in delegated}
            for r in q.list_for_session(sid)
        ]
        self.assertTrue(any(r.job_id == job_id for r in all_rows))
        self.assertTrue(all(r.job_type == JOB_TYPE_CODING_EXECUTE for r in all_rows))
        self.assertTrue(all(r.payload.get("draft_pr_only") for r in all_rows))


if __name__ == "__main__":
    unittest.main()
