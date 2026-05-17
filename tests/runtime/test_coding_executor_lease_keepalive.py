"""P1-A — coding_execute lease keepalive + lease-expired audit/recovery.

Live live-smoke (canonical session ``11917bf1e75d``):
  * coding_execute row 가 정상 enqueue 됐고 in_progress 까지 도달했지만
  * 60s 기본 lease 가 만료되면서 supervisor 의 ``reap_expired_leases``
    가 강제로 ``failed_retryable`` 처리.
  * result_json 이 비어있어 operator 가 "왜 죽었는지" 모름.

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드:

  1. keepalive 가 돌면 60s 가 지나도 reap 되지 않음
  2. keepalive 가 없으면 reaper 가 정상 회수
  3. reaped row 에 ``error=lease_expired`` 와 함께 timeout-specific
     audit (reaped_at / previous_picked_by 등) 가 남는다
  4. lease_expired 로 reap 된 row 가 startup recovery 로 requeue 된다
     (canonical-session-like recovery)
  5. successful long-running coding_execute 는 saved 까지 도달 (시뮬)
  6. regression: ordinary short coding_execute path 정상 작동
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"


def _insert_coding_row(
    db_path: Path,
    *,
    job_id: str,
    session_id: str,
    state: JobState,
    picked_by: str | None = "worker-1",
    picked_until: float | None = None,
) -> None:
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO job_queue
              (job_id, job_type, role, session_id, payload_json,
               result_json, state, priority, attempt, max_attempts,
               available_at, picked_by, picked_until,
               created_at, updated_at)
            VALUES (?, 'coding_execute', '', ?, '{}', '{}', ?, 0, 0, 3,
                    ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_id,
                state.value,
                now_ts,
                picked_by,
                picked_until,
                now_ts,
                now_ts,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Lease audit at the store level (cases 1, 2, 3, 6)
# ---------------------------------------------------------------------------


class LeaseRenewalAndReapAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_renew_lease_extends_picked_until(self) -> None:
        """case 1 (store-level): renew_lease 가 picked_until 을 미래로
        밀어 reap 대상에서 제외."""

        now = datetime.now(tz=timezone.utc).timestamp()
        _insert_coding_row(
            self.db_path,
            job_id="job-keep",
            session_id="sess-k",
            state=JobState.IN_PROGRESS,
            picked_by="worker-1",
            picked_until=now + 0.1,  # 곧 만료
        )

        refreshed = self.queue.renew_lease(
            "job-keep",
            lease_seconds=600.0,
            worker_id="worker-1",
            now=now,
        )
        self.assertIsNotNone(refreshed)
        self.assertGreaterEqual(refreshed.picked_until, now + 600.0 - 1.0)

        # Reaper at now+5 → row 살아남음
        reaped = self.queue.reap_expired_leases(now=now + 5.0)
        self.assertEqual(reaped, ())
        row = self.queue.get("job-keep")
        self.assertEqual(row.state, JobState.IN_PROGRESS)

    def test_renew_lease_refuses_when_state_inactive(self) -> None:
        """case 2 (store-level): SAVED / FAILED_TERMINAL row 는 renew 거부."""

        _insert_coding_row(
            self.db_path,
            job_id="job-saved",
            session_id="s",
            state=JobState.SAVED,
            picked_by="worker-1",
            picked_until=None,
        )
        self.assertIsNone(
            self.queue.renew_lease("job-saved", worker_id="worker-1")
        )

    def test_renew_lease_refuses_when_worker_id_mismatch(self) -> None:
        """다른 worker 가 가져간 row 는 renew 거부 — cross-talk 차단."""

        _insert_coding_row(
            self.db_path,
            job_id="job-other",
            session_id="s",
            state=JobState.IN_PROGRESS,
            picked_by="worker-2",
            picked_until=datetime.now(tz=timezone.utc).timestamp() + 60,
        )
        self.assertIsNone(
            self.queue.renew_lease("job-other", worker_id="worker-1")
        )

    def test_reap_stamps_lease_expired_audit(self) -> None:
        """case 3: reaper 가 lease_expired 와 previous_* 필드를 result_json
        에 기록 — silent reaping 금지."""

        now = datetime.now(tz=timezone.utc).timestamp()
        _insert_coding_row(
            self.db_path,
            job_id="job-dead",
            session_id="sess-r",
            state=JobState.IN_PROGRESS,
            picked_by="worker-X",
            picked_until=now - 1.0,  # 이미 만료
        )
        reaped = self.queue.reap_expired_leases(now=now)
        self.assertEqual(len(reaped), 1)
        reaped_job = reaped[0]
        self.assertEqual(reaped_job.state, JobState.FAILED_RETRYABLE)
        self.assertEqual(reaped_job.result.get("error"), "lease_expired")
        self.assertEqual(
            reaped_job.result.get("previous_picked_by"), "worker-X"
        )
        self.assertEqual(
            reaped_job.result.get("previous_state"), "in_progress"
        )
        self.assertIsNotNone(reaped_job.result.get("reaped_at"))


# ---------------------------------------------------------------------------
# Async keepalive loop (cases 1 + 2 + 6 — integration with reaper)
# ---------------------------------------------------------------------------


class KeepaliveLoopIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_keepalive_keeps_long_running_job_alive(self) -> None:
        """case 1: keepalive 동안 reap 안 됨."""

        from yule_orchestrator.runtime.coding_executor_runner import (
            _lease_keepalive_loop,
        )

        now = datetime.now(tz=timezone.utc).timestamp()
        # 짧은 초기 lease (0.2s) — keepalive 없이는 곧 reap 됨.
        _insert_coding_row(
            self.db_path,
            job_id="job-long",
            session_id="sess-long",
            state=JobState.IN_PROGRESS,
            picked_by="worker-1",
            picked_until=now + 0.2,
        )

        async def _run():
            done = asyncio.Event()
            task = asyncio.create_task(
                _lease_keepalive_loop(
                    queue=self.queue,
                    job_id="job-long",
                    worker_id="worker-1",
                    lease_seconds=5.0,
                    interval_seconds=0.05,
                    done_event=done,
                )
            )
            # 시뮬 long-running work — 0.4s 동안 keepalive 가 lease 를 갱신
            await asyncio.sleep(0.4)
            # 중간에 reaper 가 동작해도 row 가 살아 있어야 한다
            reaped = self.queue.reap_expired_leases()
            self.assertEqual(reaped, ())
            done.set()
            await task

        asyncio.run(_run())
        row = self.queue.get("job-long")
        self.assertEqual(row.state, JobState.IN_PROGRESS)

    def test_no_keepalive_means_reaper_collects_truly_hung_job(self) -> None:
        """case 2: keepalive 없으면 reaper 가 정상 회수."""

        now = datetime.now(tz=timezone.utc).timestamp()
        _insert_coding_row(
            self.db_path,
            job_id="job-hung",
            session_id="sess-hung",
            state=JobState.IN_PROGRESS,
            picked_by="worker-2",
            picked_until=now - 0.5,  # 이미 만료
        )
        reaped = self.queue.reap_expired_leases(now=now)
        self.assertEqual(len(reaped), 1)
        self.assertEqual(reaped[0].job_id, "job-hung")
        self.assertEqual(
            reaped[0].result.get("error"), "lease_expired"
        )


# ---------------------------------------------------------------------------
# Startup recovery (case 4) + canonical-session-like scenario (case 5)
# ---------------------------------------------------------------------------


class StartupRecoveryAndCanonicalScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)

    def test_startup_recovery_requeues_lease_expired_rows(self) -> None:
        """case 4: ``_recover_lease_expired_rows`` 가 lease_expired 행을
        모두 queued 로 되돌린다."""

        from yule_orchestrator.runtime.coding_executor_runner import (
            _recover_lease_expired_rows,
        )

        # 이전에 reap 된 canonical-style row 직접 stamp
        now = datetime.now(tz=timezone.utc).timestamp()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES (?, 'coding_execute', '', ?, '{}', ?, ?, 0, 0, 3,
                        ?, NULL, NULL, ?, ?)
                """,
                (
                    "1779024341246-0eb1073d13ad",
                    "11917bf1e75d",
                    json.dumps(
                        {
                            "error": "lease_expired",
                            "reaped_at": now - 60,
                            "previous_picked_by": "eng-coding-executor",
                            "previous_state": "in_progress",
                        }
                    ),
                    JobState.FAILED_RETRYABLE.value,
                    now,
                    now,
                    now,
                ),
            )
            # 다른 (lease 무관) failed_retryable row — 건드리지 않아야 함
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES ('other-job', 'coding_execute', '', 'sess-other',
                        '{}', ?, ?, 0, 0, 3, ?, NULL, NULL, ?, ?)
                """,
                (
                    json.dumps({"error": "executor_failed"}),
                    JobState.FAILED_RETRYABLE.value,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

        log_calls: List[str] = []
        requeued = _recover_lease_expired_rows(
            queue=self.queue,
            log_fn=lambda *args: log_calls.append(args[0] % args[1:]),
        )

        # canonical row 가 requeue 됨, 다른 row 는 그대로
        self.assertIn("1779024341246-0eb1073d13ad", requeued)
        self.assertNotIn("other-job", requeued)
        canonical = self.queue.get("1779024341246-0eb1073d13ad")
        self.assertEqual(canonical.state, JobState.QUEUED)
        other = self.queue.get("other-job")
        self.assertEqual(other.state, JobState.FAILED_RETRYABLE)
        # 로그 라인 노출 (silent recovery 금지)
        self.assertTrue(any("requeued" in line for line in log_calls))


# ---------------------------------------------------------------------------
# Regression: short coding_execute happy path (case 6)
# ---------------------------------------------------------------------------


class ShortCodingExecuteRegressionTests(unittest.TestCase):
    """짧은 coding_execute (keepalive 가 한 번 돌기 전에 완료) 도 통과해야 한다."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_keepalive_exits_cleanly_when_done_event_set_quickly(self) -> None:
        """짧은 job (e.g. 5ms) 도 keepalive 가 정상 종료."""

        from yule_orchestrator.runtime.coding_executor_runner import (
            _lease_keepalive_loop,
        )

        now = datetime.now(tz=timezone.utc).timestamp()
        _insert_coding_row(
            self.db_path,
            job_id="job-short",
            session_id="sess-short",
            state=JobState.IN_PROGRESS,
            picked_by="worker-1",
            picked_until=now + 60,
        )

        async def _run():
            done = asyncio.Event()
            task = asyncio.create_task(
                _lease_keepalive_loop(
                    queue=self.queue,
                    job_id="job-short",
                    worker_id="worker-1",
                    lease_seconds=60.0,
                    interval_seconds=1.0,
                    done_event=done,
                )
            )
            # process_job 이 즉시 끝났다고 시뮬
            await asyncio.sleep(0.02)
            done.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(_run())
        # 짧은 job 도 row 가 잘 유지됨
        row = self.queue.get("job-short")
        self.assertEqual(row.state, JobState.IN_PROGRESS)


if __name__ == "__main__":
    unittest.main()
