"""Live smoke fix — github_work_order executor 가 runtime inventory 에 있고
실제 큐 job 을 drain 하는지 회귀 핀 (P0-T).

라이브 관찰:
  - 라이브에서 승인 reply 후 ``github_work_order queued=1`` 상태로 정체.
  - reply routing (PR #177 의 fix) 은 붙었지만 큐 consumer 가 inventory 에
    없어 spawn 안 됨.

본 PR fix:
  1. ``ServiceKind.GITHUB_WORK_ORDER_EXECUTOR`` enum + ServiceSpec 등록
  2. ``run_service`` 가 spec.kind == GITHUB_WORK_ORDER_EXECUTOR 일 때
     ``GitHubWorkOrderWorker`` + ``run_until_shutdown`` 호출
  3. ``_KIND_TO_JOB_TYPE`` 매핑 추가 — status surface 가 queued/in_progress/
     saved 카운트를 jobs 섹션에 표시
  4. ``run_until_shutdown`` background loop — heartbeat 도 같이 stamp
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.github_workos.audit import OUTCOME_OK
from yule_engineering.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    dispatch_github_work_order,
    JOB_TYPE_GITHUB_WORK_ORDER,
)
from yule_engineering.agents.job_queue.github_work_order_executor import (
    CREATED_VIA_AUTO_CREATE,
    CREATED_VIA_EXISTING_ANCHOR,
    GitHubWorkOrderWorker,
    SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
    run_until_shutdown,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.runtime.services import (
    ENGINEERING_PROFILE,
    ServiceKind,
)
from yule_engineering.runtime.status import (
    _KIND_TO_JOB_TYPE,
    build_runtime_status,
)


# ---------------------------------------------------------------------------
# Inventory registration
# ---------------------------------------------------------------------------


class InventoryRegistrationTests(unittest.TestCase):
    def test_engineering_profile_includes_work_order_executor(self) -> None:
        ids = {spec.service_id for spec in ENGINEERING_PROFILE}
        self.assertIn(
            "eng-github-work-order-executor",
            ids,
            "P0-T: github_work_order 큐 consumer 가 inventory 에서 빠짐 "
            "— runtime up 으로 spawn 되지 않아 라이브 시나리오 정체",
        )

    def test_work_order_executor_uses_dedicated_kind(self) -> None:
        spec = next(
            s
            for s in ENGINEERING_PROFILE
            if s.service_id == "eng-github-work-order-executor"
        )
        self.assertEqual(spec.kind, ServiceKind.GITHUB_WORK_ORDER_EXECUTOR)
        self.assertTrue(spec.auto_spawn, "must be auto-spawn (no opt-in flag)")

    def test_kind_to_job_type_includes_work_order(self) -> None:
        self.assertEqual(
            _KIND_TO_JOB_TYPE.get(ServiceKind.GITHUB_WORK_ORDER_EXECUTOR),
            "github_work_order",
            "status surface 에서 jobs 섹션의 github_work_order 큐 카운트가 "
            "executor 와 연결되지 않으면 operator 가 정체를 못 본다",
        )


# ---------------------------------------------------------------------------
# Status surface — work_order executor 가 ServiceStatus 로 surface
# ---------------------------------------------------------------------------


class StatusSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)

    def test_status_surface_lists_executor_unknown_without_heartbeat(self) -> None:
        report = build_runtime_status(
            profile="engineering",
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=0,
        )
        svc = next(
            (
                s
                for s in report.services
                if s.service_id == "eng-github-work-order-executor"
            ),
            None,
        )
        self.assertIsNotNone(svc, "executor 가 status surface 에 없음")
        assert svc is not None
        self.assertEqual(svc.kind, "github_work_order_executor")
        self.assertEqual(svc.job_type, "github_work_order")

    def test_status_surface_alive_after_heartbeat(self) -> None:
        self.heartbeats.record(
            SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR,
            pid=1234,
            metadata={"state": "online"},
        )
        report = build_runtime_status(
            profile="engineering",
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=0,
        )
        svc = next(
            s
            for s in report.services
            if s.service_id == "eng-github-work-order-executor"
        )
        self.assertEqual(svc.health, "alive")


# ---------------------------------------------------------------------------
# run_until_shutdown — background loop drains queued job + heartbeat
# ---------------------------------------------------------------------------


@dataclass
class _SessionFake:
    session_id: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _WriterResult:
    ok: bool = True
    outcome: str = OUTCOME_OK
    succeeded: bool = True
    body: Mapping[str, Any] = field(default_factory=dict)


class _StubWriter:
    def __init__(self) -> None:
        self.calls: List[Mapping[str, Any]] = []
        self._next = 77

    def create_issue(self, **kwargs):
        self.calls.append(kwargs)
        n = self._next
        self._next += 1
        return _WriterResult(
            body={
                "number": n,
                "html_url": f"https://github.com/{kwargs['repo']}/issues/{n}",
                "url": f"https://api.github.com/repos/{kwargs['repo']}/issues/{n}",
            },
        )


class RunUntilShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)
        self.sessions: Dict[str, _SessionFake] = {
            "sess-live": _SessionFake(
                session_id="sess-live",
                extra={
                    # coding_proposal stub — continuation 이 promote 가능하도록
                    "coding_proposal": {
                        "session_id": "sess-live",
                        "user_request": "live smoke 회원가입 구현",
                        "executor_role": "backend-engineer",
                        "review_roles": ["tech-lead"],
                        "participant_roles": ["backend-engineer", "tech-lead"],
                        "write_scope": ["src/api/auth"],
                        "forbidden_scope": [".env"],
                        "safety_rules": ["no force push"],
                        "reason": "live smoke",
                        "approval_required": True,
                        "metadata": {},
                        "lifecycle_mode": "implementation",
                    },
                },
            ),
        }
        self.writer = _StubWriter()
        self.worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (self.writer, "L2"),
            heartbeats=self.heartbeats,
            load_session_fn=lambda sid: self.sessions.get(sid),
            update_session_fn=self._update_session,
        )

    def _update_session(self, session, new_extra):
        session.extra = dict(new_extra)
        self.sessions[session.session_id] = session
        return session

    def _enqueue_work_order(self) -> str:
        wo = GitHubWorkOrder(
            proposal_id="p-live",
            session_id="sess-live",
            approval_id="a-live",
            approved_by="masterway",
            approved_at="2026-05-16T13:00:00+00:00",
            request_summary="live full-stack",
            repo="yule-studio/naver-search-clone",
            dry_run=False,
            issue_auto_create_plan={
                "title": "[기능] 회원가입",
                "body": "## 어떤 기능인가요?\n> live\n",
                "labels": ["✨ Feature"],
                "assignees": [],
                "template_path": ".github/ISSUE_TEMPLATE/feature.md",
                "confidence": "high",
                "audit_reason": "template_used",
                "needs_operator_decision": False,
                "template_score": 2,
            },
        )
        outcome = dispatch_github_work_order(self.queue, wo)
        assert outcome.job is not None
        return outcome.job.job_id

    def test_run_until_shutdown_drains_queued_job(self) -> None:
        """라이브 시나리오 핵심 회귀: queued github_work_order job 이
        run_until_shutdown loop 에 의해 실제로 drain 되고 issue 가 생성된다."""

        self._enqueue_work_order()

        async def driver():
            shutdown = asyncio.Event()

            async def trigger_shutdown():
                # 한 두 번 polling 후 shutdown
                await asyncio.sleep(0.05)
                shutdown.set()

            asyncio.create_task(trigger_shutdown())
            await run_until_shutdown(
                self.worker,
                shutdown_event=shutdown,
                interval_seconds=0.01,
                heartbeats=self.heartbeats,
                heartbeat_interval_seconds=0.01,
            )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(driver())
        finally:
            loop.close()

        # writer.create_issue 호출됨
        self.assertEqual(len(self.writer.calls), 1)
        # session.extra anchor + coding_job=ready 모두 stamp
        sess = self.sessions["sess-live"]
        self.assertIn(SESSION_EXTRA_GITHUB_ISSUE_KEY, sess.extra)
        anchor = sess.extra[SESSION_EXTRA_GITHUB_ISSUE_KEY]
        self.assertEqual(anchor["issue_number"], 77)
        self.assertEqual(anchor["created_via"], CREATED_VIA_AUTO_CREATE)
        # continuation 도 발동 — coding_job=ready
        self.assertIn("coding_job", sess.extra)
        self.assertEqual(sess.extra["coding_job"]["status"], "ready")
        # heartbeat 도 기록됨 — status surface ALIVE
        hb = self.heartbeats.get(SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR)
        self.assertIsNotNone(hb)
        assert hb is not None
        self.assertEqual(hb.metadata.get("state"), "online")

    def test_run_until_shutdown_records_heartbeat_even_when_queue_empty(self) -> None:
        """큐가 비어있어도 heartbeat 는 살아있어야 status surface ALIVE."""

        async def driver():
            shutdown = asyncio.Event()

            async def trigger_shutdown():
                await asyncio.sleep(0.02)
                shutdown.set()

            asyncio.create_task(trigger_shutdown())
            await run_until_shutdown(
                self.worker,
                shutdown_event=shutdown,
                interval_seconds=0.01,
                heartbeats=self.heartbeats,
                heartbeat_interval_seconds=0.01,
            )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(driver())
        finally:
            loop.close()

        hb = self.heartbeats.get(SERVICE_ID_GITHUB_WORK_ORDER_EXECUTOR)
        self.assertIsNotNone(hb)


if __name__ == "__main__":
    unittest.main()
