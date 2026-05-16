"""Runtime status surface visibility fix 회귀 핀 — P0-T.

라이브 관찰 (operator 보고):
- `eng-supervisor-watch` 가 항상 UNKNOWN
- `eng-discord-gateway` 가 실제 붙어 있어도 UNKNOWN
- `eng-member-*` 가 graceful-disable / online / unknown 을 구분 못 함
- `completion funnel` 이 status CLI 에서 항상 비어 보임

본 test 가 통과 = status surface 가 "사람이 믿을 수 있는 운영 화면" 으로
유지되는지 가장 먼저 잡힌다.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.agents.job_queue.worker_loop import (
    run_supervisor_watch_loop,
)
from yule_orchestrator.runtime.status import (
    ACTION_KIND_GRACEFUL_DISABLED,
    ACTION_KIND_UNKNOWN_SERVICE,
    HEALTH_ALIVE,
    HEALTH_GRACEFUL_DISABLED,
    HEALTH_UNKNOWN,
    build_runtime_status,
    summarize_operator_actions,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Supervisor heartbeat — eng-supervisor-watch 가 sweep iteration 시작 시
# 자기 자신의 heartbeat 를 기록한다.
# ---------------------------------------------------------------------------


class SupervisorHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)

    def test_supervisor_iteration_records_heartbeat(self) -> None:
        async def no_sleep(_secs):
            return None

        def _empty_sweep(*, heartbeat_store, job_queue, deadline_seconds):
            return SimpleNamespace(stale=(), reaped_jobs=0)

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sleep_fn=no_sleep,
                max_iterations=1,
            )
        )

        record = self.heartbeats.get("eng-supervisor-watch")
        self.assertIsNotNone(
            record,
            "supervisor watch loop 이 자체 heartbeat 를 기록하지 않음 — "
            "status surface 에서 영원히 UNKNOWN",
        )
        self.assertEqual(record.metadata.get("iteration"), 1)

    def test_status_surface_marks_supervisor_alive_after_iteration(self) -> None:
        async def no_sleep(_secs):
            return None

        def _empty_sweep(*, heartbeat_store, job_queue, deadline_seconds):
            return SimpleNamespace(stale=(), reaped_jobs=0)

        _run(
            run_supervisor_watch_loop(
                heartbeat_store=self.heartbeats,
                job_queue=self.queue,
                sweep_fn=_empty_sweep,
                sleep_fn=no_sleep,
                max_iterations=1,
            )
        )

        report = build_runtime_status(
            profile="engineering",
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=0,
        )
        sup = next(
            (s for s in report.services if s.service_id == "eng-supervisor-watch"),
            None,
        )
        self.assertIsNotNone(sup)
        assert sup is not None
        self.assertEqual(
            sup.health,
            HEALTH_ALIVE,
            "supervisor heartbeat 가 기록됐는데도 status 가 ALIVE 아님",
        )


# ---------------------------------------------------------------------------
# Graceful-disable 분류 — member bot 이 token 없어 graceful-disable 된
# 경우 UNKNOWN 이 아니라 GRACEFUL_DISABLED 로 분류돼야 한다.
# ---------------------------------------------------------------------------


class GracefulDisableSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "q.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)

    def test_graceful_disabled_member_bot_shows_as_disabled_not_unknown(self) -> None:
        # member bot 의 graceful-disable heartbeat metadata 를 직접 stamp.
        # run_service 의 `_record_graceful_disable` 이 동일한 형태로 기록.
        self.heartbeats.record(
            "eng-member-backend-engineer",
            pid=1234,
            metadata={
                "state": "graceful_disabled",
                "env_key": "ENGINEERING_AGENT_BACKEND_ENGINEER_BOT_TOKEN",
                "reason": "token_missing",
            },
        )

        report = build_runtime_status(
            profile="engineering",
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=0,
        )
        member = next(
            (
                s
                for s in report.services
                if s.service_id == "eng-member-backend-engineer"
            ),
            None,
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(
            member.health,
            HEALTH_GRACEFUL_DISABLED,
            "graceful_disabled metadata 가 ALIVE 또는 UNKNOWN 으로 잘못 "
            "분류됨 — operator 가 token vs restart 구분 못 함",
        )
        self.assertEqual(
            member.metadata.get("env_key"),
            "ENGINEERING_AGENT_BACKEND_ENGINEER_BOT_TOKEN",
        )

    def test_operator_actions_distinguish_disabled_from_unknown(self) -> None:
        # 1 member graceful-disabled, 1 member never started
        self.heartbeats.record(
            "eng-member-backend-engineer",
            pid=1234,
            metadata={
                "state": "graceful_disabled",
                "env_key": "ENGINEERING_AGENT_BACKEND_ENGINEER_BOT_TOKEN",
                "reason": "token_missing",
            },
        )
        # eng-member-frontend-engineer 는 heartbeat 없음 → UNKNOWN

        report = build_runtime_status(
            profile="engineering",
            queue=self.queue,
            heartbeats=self.heartbeats,
            failed_limit=0,
        )
        actions = summarize_operator_actions(report)
        action_kinds = {a.kind for a in actions}
        self.assertIn(
            ACTION_KIND_GRACEFUL_DISABLED,
            action_kinds,
            f"graceful-disabled operator action 누락 — got {action_kinds}",
        )
        self.assertIn(
            ACTION_KIND_UNKNOWN_SERVICE,
            action_kinds,
            f"never-started UNKNOWN action 도 따로 있어야 함 — got {action_kinds}",
        )
        # graceful_disabled action 의 next_step 이 env / token 에 대해 안내
        disabled_action = next(
            a for a in actions if a.kind == ACTION_KIND_GRACEFUL_DISABLED
        )
        self.assertIn(".env.local", disabled_action.next_step)
        # unknown_service action 의 next_step 은 restart 안내
        unknown_action = next(
            a for a in actions if a.kind == ACTION_KIND_UNKNOWN_SERVICE
        )
        self.assertIn("yule runtime up", unknown_action.next_step)


# ---------------------------------------------------------------------------
# CLI completion funnel collector
# ---------------------------------------------------------------------------


class StatusCLICompletionFunnelTests(unittest.TestCase):
    def test_cli_loads_completion_funnel_recent_via_fallback_lister(self) -> None:
        """status_cli 가 build_runtime_status 에 completion_funnel_recent
        를 실제로 전달하는지 — 빈 lister 라도 호출 path 가 살아있음."""

        from yule_orchestrator.runtime.status_cli import (
            _load_completion_funnel_safe,
        )

        called: List[Any] = []

        def _stub_lister(*, limit: int = 50):
            called.append({"limit": limit})
            # 한 session 에 completion_funnel.history 한 줄 보유
            return [
                SimpleNamespace(
                    session_id="sess-x",
                    extra={
                        "completion_funnel": {
                            "history": [
                                {
                                    "job_id": "j1",
                                    "job_type": "research_collect",
                                    "completion_status": "done",
                                    "ticked": True,
                                    "reason": "ok",
                                    "recommended_source": "supervisor",
                                    "next_kind": None,
                                    "decided_at": "2026-05-16T10:00:00+00:00",
                                }
                            ]
                        }
                    },
                )
            ]

        recent = _load_completion_funnel_safe(fallback_lister=_stub_lister)
        self.assertTrue(called)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].job_id, "j1")
        self.assertEqual(recent[0].completion_status, "done")

    def test_cli_falls_back_to_empty_on_lister_failure(self) -> None:
        from yule_orchestrator.runtime.status_cli import (
            _load_completion_funnel_safe,
        )

        def _boom(*, limit: int = 50):
            raise RuntimeError("session store down")

        # 절대 raise 하지 않음 — CLI 가 crash 하지 않게
        recent = _load_completion_funnel_safe(fallback_lister=_boom)
        self.assertEqual(recent, ())


if __name__ == "__main__":
    unittest.main()
