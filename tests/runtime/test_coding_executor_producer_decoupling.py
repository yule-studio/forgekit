"""P0-Y — coding_execute producer/consumer 분리 + marker 정확성.

라이브 canonical session ``11917bf1e75d`` 가 coding_job=ready 까지 도달
했지만 ``coding_execute queued=0`` 인 채로 멈춘 회귀의 원인은
``dispatch_ready_coding_jobs`` 가 consumer ``_process(job)`` 안에서만
호출되던 chicken-and-egg deadlock 이었다.

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드:

  1. ready coding_job + 빈 coding_execute → 별도 producer tick 이 enqueue
  2. coding_execute job 이 없는 상태에서도 producer 가 동작 (생존)
  3. ``coding_dispatch_queued`` marker 는 실제 enqueue 후에만 stamp
  4. recovered session 이 새 intake 없이도 producer tick 으로 enqueue
  5. stale session metadata (write_blocked_reason 등) 가 repair 시 정리
  6. 기존 coding_execute 흐름 회귀 없음 (dispatcher 가 똑같이 work)
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.job import STATUS_READY, build_coding_job_from_proposal
from yule_engineering.agents.coding.authorization import (
    CodingAuthorizationProposal,
    proposal_from_dict,
)
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    SESSION_EXTRA_DISPATCH_KEY,
    dispatch_ready_coding_jobs,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.job_queue.work_order_coding_continuation import (
    PROGRESS_CODING_DISPATCH_QUEUED,
    PROGRESS_CODING_JOB_READY,
    SESSION_EXTRA_CODING_JOB_KEY,
    SESSION_EXTRA_PROGRESS_KEY,
    repair_session_for_coding_dispatch,
)


_REPO = "yule-studio/naver-search-clone"
_PROMPT = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버 검색 풀스택 MVP 구현해줘. 프론트 / 백엔드 / 데이터베이스 / 도커."
)


def _proposal_payload(session_id: str) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "user_request": _PROMPT,
        "executor_role": "backend-engineer",
        "review_roles": ["tech-lead"],
        "participant_roles": ["backend-engineer", "tech-lead"],
        "write_scope": [],
        "forbidden_scope": [],
        "reason": "p0-y test",
        "safety_rules": [],
        "approval_required": True,
        "metadata": {},
        "lifecycle_mode": "implementation",
        "research_leads": [],
    }


def _ready_coding_job(session_id: str) -> Dict[str, Any]:
    proposal = proposal_from_dict(_proposal_payload(session_id))
    job = build_coding_job_from_proposal(
        proposal,
        status=STATUS_READY,
        approved_at=datetime.now(tz=timezone.utc),
    )
    payload = dict(job.to_dict())
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "issue_number": 1,
            "repo_full_name": _REPO,
            "base_branch": "main",
            "dry_run": False,
            "approval_id": "approval-1",
        }
    )
    payload["metadata"] = metadata
    payload["status"] = STATUS_READY
    return payload


@dataclass
class _SessionFake:
    session_id: str
    prompt: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _build_executor_worker(queue: JobQueue, heartbeats: HeartbeatStore) -> CodingExecutorWorker:
    """Minimal executor worker so dispatch_ready_coding_jobs can enqueue
    against a real queue. process_job 은 본 테스트에선 호출 안 함."""

    return CodingExecutorWorker(queue=queue, heartbeats=heartbeats)


# ---------------------------------------------------------------------------
# 1+2: producer tick works when queue is empty
# ---------------------------------------------------------------------------


class ProducerWorksWithEmptyQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = _build_executor_worker(self.queue, self.heartbeats)

    def _seed_ready_session(self, session_id: str) -> _SessionFake:
        return _SessionFake(
            session_id=session_id,
            prompt=_PROMPT,
            extra={
                "coding_proposal": _proposal_payload(session_id),
                "coding_job": _ready_coding_job(session_id),
                "github_work_order_issue": {
                    "issue_number": 1,
                    "repo": _REPO,
                    "created_via": "auto_create",
                },
            },
        )

    def test_producer_enqueues_when_coding_execute_queue_empty(self) -> None:
        """canonical scenario — coding_execute 큐 0개 + ready session 1개
        → producer tick 한 번이면 queue row 생성."""

        session = self._seed_ready_session("11917bf1e75d")
        counts = self.queue.count_by_type_and_state()
        self.assertEqual(
            counts.get((JOB_TYPE_CODING_EXECUTE, JobState.QUEUED.value), 0),
            0,
        )

        dispatched = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [session],
        )
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(dispatched[0].created)
        self.assertEqual(dispatched[0].session_id, "11917bf1e75d")
        # 실제 queue row 가 생성됨
        counts_after = self.queue.count_by_type_and_state()
        self.assertGreaterEqual(
            counts_after.get((JOB_TYPE_CODING_EXECUTE, JobState.QUEUED.value), 0),
            1,
        )

    def test_producer_dispatches_marker_only_after_actual_enqueue(self) -> None:
        """P0-Y marker correctness — dispatch_queued progress marker 는
        ``dispatch_ready_coding_jobs`` 가 실제 queue row 만든 직후에만."""

        session = self._seed_ready_session("11917bf1e75d")
        # before: ready stamp 직후 marker (coding_job_ready) 만 있다고 가정
        session.extra[SESSION_EXTRA_PROGRESS_KEY] = {
            PROGRESS_CODING_JOB_READY: {"at": "2026-05-17T11:00:00+00:00", "detail": {}}
        }
        self.assertNotIn(
            PROGRESS_CODING_DISPATCH_QUEUED,
            session.extra[SESSION_EXTRA_PROGRESS_KEY],
        )

        dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [session],
            update_session_fn=lambda updated, *, now: setattr(
                session, "extra", dict(updated.extra)
            ),
        )

        # dispatch marker + progress marker 둘 다 추가됨
        self.assertIn(SESSION_EXTRA_DISPATCH_KEY, session.extra)
        progress = session.extra.get(SESSION_EXTRA_PROGRESS_KEY) or {}
        self.assertIn(PROGRESS_CODING_DISPATCH_QUEUED, progress)
        # marker detail 에 진짜 job_id 가 들어감 — 빈 값 아님
        detail = progress[PROGRESS_CODING_DISPATCH_QUEUED]["detail"]
        self.assertTrue(detail.get("job_id"))


# ---------------------------------------------------------------------------
# 3: marker only after enqueue (positive + negative)
# ---------------------------------------------------------------------------


class MarkerCorrectnessTests(unittest.TestCase):
    def test_promote_alone_does_not_stamp_dispatch_queued(self) -> None:
        """`promote_session_to_coding_ready` 자체는 절대 dispatch_queued
        stamp 하면 안 됨 (queue row 존재 보장 없음)."""

        from yule_engineering.agents.job_queue.work_order_coding_continuation import (
            promote_session_to_coding_ready,
        )

        outcome = promote_session_to_coding_ready(
            session_extra={"coding_proposal": _proposal_payload("s")},
            anchor={"issue_number": 1, "created_via": "auto_create"},
            repo=_REPO,
            base_branch="main",
            dry_run=True,
        )
        self.assertTrue(outcome.promoted)
        self.assertIn(PROGRESS_CODING_JOB_READY, outcome.progress_markers)
        self.assertNotIn(
            PROGRESS_CODING_DISPATCH_QUEUED, outcome.progress_markers
        )


# ---------------------------------------------------------------------------
# 4+5: recovered session can advance + stale metadata normalized
# ---------------------------------------------------------------------------


class RecoveredSessionAdvancementTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = _build_executor_worker(self.queue, self.heartbeats)

    def test_repaired_session_with_stale_block_reason_normalized(self) -> None:
        """canonical session 시뮬: task_type=qa-test + write_blocked_reason
        에 qa-engineer wording 남아있음 → repair 후 둘 다 정리."""

        session = _SessionFake(
            session_id="11917bf1e75d",
            prompt=_PROMPT,
            extra={
                "github_work_order_issue": {
                    "issue_number": 1,
                    "repo": _REPO,
                    "created_via": "auto_create",
                    "approval_id": "approval-1",
                    "approved_by": "operator",
                    "approved_at": "2026-05-17T11:00:00+00:00",
                    "dry_run": False,
                    "html_url": f"https://github.com/{_REPO}/issues/1",
                },
            },
        )
        session.task_type = "qa-test"
        session.executor_role = "qa-engineer"
        session.write_blocked_reason = (
            "write is requested for qa-engineer but user_approved=False"
        )
        store: Dict[str, _SessionFake] = {session.session_id: session}

        outcome = repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=lambda sid: store.get(sid),
            update_session_fn=lambda s, _e: store.__setitem__(s.session_id, s),
        )
        self.assertTrue(outcome.promoted)
        repaired = store[session.session_id]
        # task_type / executor_role 재분류
        self.assertEqual(repaired.task_type, "full-stack-app")
        self.assertEqual(repaired.executor_role, "backend-engineer")
        # P0-Y session normalization — stale qa-engineer wording 정리
        self.assertIsNone(repaired.write_blocked_reason)

    def test_repaired_session_picked_up_by_producer_without_new_intake(
        self,
    ) -> None:
        """canonical scenario end-to-end:
          - repair 가 session 을 정리 + coding_job=ready 로 promote
          - 별도 producer tick (dispatch_ready_coding_jobs) 만으로
            coding_execute queue 에 row 가 생긴다
          - operator 가 새 intake 를 다시 넣을 필요 없음.
        """

        session = _SessionFake(
            session_id="11917bf1e75d",
            prompt=_PROMPT,
            extra={
                "github_work_order_issue": {
                    "issue_number": 1,
                    "repo": _REPO,
                    "created_via": "auto_create",
                    "approval_id": "approval-1",
                    "approved_by": "operator",
                    "approved_at": "2026-05-17T11:00:00+00:00",
                    "dry_run": False,
                    "html_url": f"https://github.com/{_REPO}/issues/1",
                },
            },
        )
        session.task_type = "qa-test"
        session.executor_role = "qa-engineer"
        store: Dict[str, _SessionFake] = {session.session_id: session}

        # Step A — repair
        repair_session_for_coding_dispatch(
            session_id=session.session_id,
            load_session_fn=lambda sid: store.get(sid),
            update_session_fn=lambda s, _e: store.__setitem__(s.session_id, s),
        )

        # Step B — independent producer tick (the wiring this PR fixes)
        repaired = store[session.session_id]
        dispatched = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [repaired],
        )
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(dispatched[0].created)
        counts_after = self.queue.count_by_type_and_state()
        self.assertGreaterEqual(
            counts_after.get((JOB_TYPE_CODING_EXECUTE, JobState.QUEUED.value), 0),
            1,
        )


# ---------------------------------------------------------------------------
# 6: regression — existing coding_execute flow still works
# ---------------------------------------------------------------------------


class ExistingFlowRegressionTests(unittest.TestCase):
    """`dispatch_ready_coding_jobs` 가 같은 시그니처/시맨틱으로 작동 확인."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = _build_executor_worker(self.queue, self.heartbeats)

    def test_idempotent_dedups_against_existing_queue_row(self) -> None:
        session = _SessionFake(
            session_id="s-dup",
            prompt=_PROMPT,
            extra={
                "coding_proposal": _proposal_payload("s-dup"),
                "coding_job": _ready_coding_job("s-dup"),
                "github_work_order_issue": {
                    "issue_number": 9,
                    "repo": _REPO,
                    "created_via": "auto_create",
                },
            },
        )
        store = {"s-dup": session}
        first = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store["s-dup"]],
            update_session_fn=lambda u, *, now: store.__setitem__(
                "s-dup", _SessionFake(
                    session_id="s-dup", prompt=_PROMPT, extra=dict(u.extra)
                )
            ),
        )
        second = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store["s-dup"]],
            update_session_fn=lambda u, *, now: store.__setitem__(
                "s-dup", _SessionFake(
                    session_id="s-dup", prompt=_PROMPT, extra=dict(u.extra)
                )
            ),
        )
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].created)
        # 두 번째 tick: dispatch marker 가 이미 있으므로 iter_ready_coding_jobs
        # 가 skip → dispatched 빈 tuple
        self.assertEqual(second, ())


# ---------------------------------------------------------------------------
# Producer loop background task — minimal smoke (asyncio)
# ---------------------------------------------------------------------------


class ProducerLoopBackgroundTaskTests(unittest.TestCase):
    """`coding_executor_runner._producer_loop` 가 shutdown 까지 살아있음."""

    def test_producer_loop_calls_dispatch_then_exits_on_shutdown(self) -> None:
        from yule_engineering.runtime.coding_executor_runner import (
            _producer_loop,
        )

        calls: List[int] = []

        def _fake_dispatch(**kwargs):  # noqa: ANN003
            calls.append(1)
            return ()

        import yule_engineering.runtime.coding_executor_runner as runner_mod

        original = runner_mod.dispatch_ready_coding_jobs
        runner_mod.dispatch_ready_coding_jobs = _fake_dispatch
        try:
            async def _run() -> None:
                shutdown = asyncio.Event()

                async def _stopper() -> None:
                    await asyncio.sleep(0.05)
                    shutdown.set()

                stopper_task = asyncio.create_task(_stopper())
                await _producer_loop(
                    worker=SimpleNamespace(),
                    shutdown_event=shutdown,
                    interval_seconds=0.01,
                )
                await stopper_task

            asyncio.run(_run())
        finally:
            runner_mod.dispatch_ready_coding_jobs = original

        # 최소 한 번은 호출됐어야 함
        self.assertGreaterEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
