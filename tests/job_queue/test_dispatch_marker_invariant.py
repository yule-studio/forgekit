"""P0-Z — cross-store invariant: session.extra['coding_execute_dispatch']
↔ job_queue row.

라이브 canonical session ``11917bf1e75d`` 의 실제 stranded 원인:
session.extra 의 ``coding_execute_dispatch.job_id`` 는 있지만 그 id 의
queue row 는 통째로 사라진 상태. producer 가 marker 만 보고 영구 skip.

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드:

  1. marker + queue row exist + active state → skip OK (no dup enqueue)
  2. marker exists + queue row missing → stale, re-enqueue
  3. marker exists + wrong session / wrong type / terminal row → stale
  4. canonical-session-like recovered case → advances without new intake
  5. status / log surface exposes stale marker (silent heal 금지)
  6. regression: valid in-flight coding job is not duplicated
"""

from __future__ import annotations

import logging
import sqlite3
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


from yule_engineering.agents.coding.authorization import proposal_from_dict
from yule_engineering.agents.coding.job import (
    STATUS_READY,
    build_coding_job_from_proposal,
)
from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
    DispatchMarkerCheck,
    JOB_TYPE_CODING_EXECUTE,
    MARKER_STATE_MISSING,
    MARKER_STATE_STALE,
    MARKER_STATE_VALID,
    PHANTOM_MARKER_REASON_NO_ROW,
    PHANTOM_MARKER_REASON_TERMINAL,
    PHANTOM_MARKER_REASON_WRONG_SESSION,
    PHANTOM_MARKER_REASON_WRONG_TYPE,
    SESSION_EXTRA_DISPATCH_KEY,
    dispatch_ready_coding_jobs,
    iter_ready_coding_jobs,
    validate_coding_dispatch_marker,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecutorWorker,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


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
        "reason": "p0-z test",
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


def _ready_session(
    session_id: str, marker: Optional[Mapping[str, Any]] = None
) -> _SessionFake:
    extra: Dict[str, Any] = {
        "coding_proposal": _proposal_payload(session_id),
        "coding_job": _ready_coding_job(session_id),
        "github_work_order_issue": {
            "issue_number": 1,
            "repo": _REPO,
            "created_via": "auto_create",
        },
    }
    if marker is not None:
        extra[SESSION_EXTRA_DISPATCH_KEY] = dict(marker)
    return _SessionFake(session_id=session_id, prompt=_PROMPT, extra=extra)


# ---------------------------------------------------------------------------
# SSoT validator — invariant truth table
# ---------------------------------------------------------------------------


class ValidateMarkerSSoTTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def _insert_row(
        self,
        *,
        job_id: str,
        job_type: str,
        session_id: str,
        state: JobState,
    ) -> None:
        # Direct insert so we can control the (job_type, session_id, state)
        # triple regardless of how the enum / state machine would otherwise
        # constrain it.
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES (?, ?, '', ?, '{}', '{}', ?, 0, 0, 3, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id, job_type, session_id, state.value,
                    now_ts, now_ts, now_ts,
                ),
            )
            conn.commit()

    def test_missing_marker_returns_missing(self) -> None:
        session = _SessionFake(session_id="s1", extra={})
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertEqual(check.state, MARKER_STATE_MISSING)

    def test_marker_with_active_row_is_valid(self) -> None:
        self._insert_row(
            job_id="job-active",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="s2",
            state=JobState.QUEUED,
        )
        session = _SessionFake(
            session_id="s2", extra={SESSION_EXTRA_DISPATCH_KEY: {"job_id": "job-active"}}
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertTrue(check.is_valid)
        self.assertEqual(check.marker_job_id, "job-active")

    def test_marker_with_missing_row_is_stale_no_row(self) -> None:
        session = _SessionFake(
            session_id="s3",
            extra={
                SESSION_EXTRA_DISPATCH_KEY: {"job_id": "phantom-id"}
            },
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertTrue(check.is_stale)
        self.assertEqual(check.reason, PHANTOM_MARKER_REASON_NO_ROW)
        self.assertEqual(check.marker_job_id, "phantom-id")

    def test_marker_pointing_to_wrong_type_is_stale(self) -> None:
        self._insert_row(
            job_id="job-wrong-type",
            job_type="approval_post",
            session_id="s4",
            state=JobState.QUEUED,
        )
        session = _SessionFake(
            session_id="s4",
            extra={SESSION_EXTRA_DISPATCH_KEY: {"job_id": "job-wrong-type"}},
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertTrue(check.is_stale)
        self.assertEqual(check.reason, PHANTOM_MARKER_REASON_WRONG_TYPE)

    def test_marker_pointing_to_wrong_session_is_stale(self) -> None:
        self._insert_row(
            job_id="job-other",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="other-session",
            state=JobState.QUEUED,
        )
        session = _SessionFake(
            session_id="s5",
            extra={SESSION_EXTRA_DISPATCH_KEY: {"job_id": "job-other"}},
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertTrue(check.is_stale)
        self.assertEqual(check.reason, PHANTOM_MARKER_REASON_WRONG_SESSION)

    def test_marker_with_terminal_row_is_classified_terminal(self) -> None:
        """P1-C: FAILED_TERMINAL / SAVED 는 stale 이 아닌 'terminal' —
        dispatch 가 이미 결과까지 도달한 상태. producer 가 새 row 만들면
        안 되며, caller 는 그대로 skip."""

        from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
            MARKER_STATE_TERMINAL,
        )

        self._insert_row(
            job_id="job-terminal",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="s6",
            state=JobState.FAILED_TERMINAL,
        )
        session = _SessionFake(
            session_id="s6",
            extra={SESSION_EXTRA_DISPATCH_KEY: {"job_id": "job-terminal"}},
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertEqual(check.state, MARKER_STATE_TERMINAL)
        self.assertFalse(check.is_stale)

    def test_marker_with_failed_retryable_row_is_pending_retry(self) -> None:
        """P1-C: FAILED_RETRYABLE 는 phantom 이 아니라 'pending_retry'.
        producer 가 새 row 를 만들면 attempt 카운터가 reset 되어
        max_attempts / backoff 가 무력화되는 infinite loop 의 원인."""

        from yule_engineering.agents.job_queue.coding_execute_dispatcher import (
            MARKER_STATE_PENDING_RETRY,
        )

        self._insert_row(
            job_id="job-retry",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="s7",
            state=JobState.FAILED_RETRYABLE,
        )
        session = _SessionFake(
            session_id="s7",
            extra={SESSION_EXTRA_DISPATCH_KEY: {"job_id": "job-retry"}},
        )
        check = validate_coding_dispatch_marker(session=session, queue=self.queue)
        self.assertEqual(check.state, MARKER_STATE_PENDING_RETRY)
        self.assertFalse(check.is_stale)


# ---------------------------------------------------------------------------
# iter_ready_coding_jobs — queue-aware skip
# ---------------------------------------------------------------------------


class IterReadyQueueAwareTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_session_with_phantom_marker_is_yielded_when_queue_aware(self) -> None:
        """case 2: marker exists, but queue row missing → yield (stale)."""

        session = _ready_session(
            "phantom-sess",
            marker={"job_id": "never-existed", "executor_role": "backend-engineer"},
        )
        # queue-unaware (옛 동작) → skip
        unaware = list(
            iter_ready_coding_jobs(session_loader=lambda: [session])
        )
        self.assertEqual(unaware, [])
        # queue-aware (P0-Z) → yield
        aware = list(
            iter_ready_coding_jobs(
                session_loader=lambda: [session], queue=self.queue
            )
        )
        self.assertEqual(len(aware), 1)
        self.assertEqual(aware[0].session_id, "phantom-sess")

    def test_session_with_valid_marker_is_skipped(self) -> None:
        """case 1+6: valid in-flight marker → skip (no duplicate)."""

        # Pre-insert an active coding_execute row for session.
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES (?, ?, '', ?, '{}', '{}', ?, 0, 0, 3, ?, NULL, NULL, ?, ?)
                """,
                (
                    "job-live",
                    JOB_TYPE_CODING_EXECUTE,
                    "live-sess",
                    JobState.QUEUED.value,
                    now_ts,
                    now_ts,
                    now_ts,
                ),
            )
            conn.commit()

        session = _ready_session(
            "live-sess",
            marker={"job_id": "job-live", "executor_role": "backend-engineer"},
        )
        aware = list(
            iter_ready_coding_jobs(
                session_loader=lambda: [session], queue=self.queue
            )
        )
        self.assertEqual(aware, [])


# ---------------------------------------------------------------------------
# dispatch_ready_coding_jobs — self-heal + audit + duplicate guard
# ---------------------------------------------------------------------------


class DispatchSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )

    def _store(self, session: _SessionFake) -> Dict[str, _SessionFake]:
        store = {session.session_id: session}

        def _update(updated, *, now=None):  # noqa: ANN001
            new = _SessionFake(
                session_id=updated.session_id,
                prompt=getattr(updated, "prompt", ""),
                extra=dict(updated.extra),
            )
            store[updated.session_id] = new

        self._update_fn = _update
        return store

    def test_phantom_marker_session_is_self_healed_and_reenqueued(self) -> None:
        """case 2+5: phantom marker → producer re-enqueues + audit token set."""

        session = _ready_session(
            "11917bf1e75d",
            marker={
                "job_id": "1779022229629-ffff03784aaf",
                "executor_role": "backend-engineer",
            },
        )
        store = self._store(session)

        with self.assertLogs(
            "yule_engineering.agents.job_queue.coding_execute_dispatcher",
            level="WARNING",
        ) as cm:
            dispatched = dispatch_ready_coding_jobs(
                worker=self.worker,
                session_loader=lambda: [store["11917bf1e75d"]],
                update_session_fn=self._update_fn,
            )

        self.assertEqual(len(dispatched), 1)
        d = dispatched[0]
        # Re-enqueue 가 일어났음
        self.assertTrue(d.created)
        self.assertNotEqual(d.job_id, "1779022229629-ffff03784aaf")
        # Audit field 가 채워짐 (operator visibility)
        self.assertEqual(d.stale_marker_reason, PHANTOM_MARKER_REASON_NO_ROW)
        self.assertEqual(
            d.stale_marker_job_id, "1779022229629-ffff03784aaf"
        )
        # Loud log — silent heal 금지
        joined = "\n".join(cm.output)
        self.assertIn("stale dispatch marker", joined)
        self.assertIn("11917bf1e75d", joined)
        self.assertIn(PHANTOM_MARKER_REASON_NO_ROW, joined)

    def test_valid_marker_session_is_not_re_enqueued(self) -> None:
        """case 6 regression: valid in-flight → no dup."""

        # First enqueue normally (no marker yet)
        session = _ready_session("sess-once")
        store = self._store(session)
        dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store["sess-once"]],
            update_session_fn=self._update_fn,
        )
        live_session = store["sess-once"]
        marker = live_session.extra.get(SESSION_EXTRA_DISPATCH_KEY)
        self.assertIsNotNone(marker)
        # Second producer tick: marker is real + queue row is active →
        # dispatcher returns empty tuple.
        second = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store["sess-once"]],
            update_session_fn=self._update_fn,
        )
        self.assertEqual(second, ())

    def test_canonical_session_advances_after_self_heal(self) -> None:
        """case 4: canonical-style stranded session → no new intake needed."""

        session = _ready_session(
            "11917bf1e75d",
            marker={
                "job_id": "1779022229629-ffff03784aaf",
                "executor_role": "backend-engineer",
            },
        )
        store = self._store(session)
        # before: empty queue
        counts = self.queue.count_by_type_and_state()
        self.assertEqual(
            counts.get((JOB_TYPE_CODING_EXECUTE, JobState.QUEUED.value), 0),
            0,
        )

        dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store["11917bf1e75d"]],
            update_session_fn=self._update_fn,
        )

        # after: row exists in queue
        counts_after = self.queue.count_by_type_and_state()
        self.assertGreaterEqual(
            counts_after.get(
                (JOB_TYPE_CODING_EXECUTE, JobState.QUEUED.value), 0
            ),
            1,
        )
        # marker overwritten with the real new job_id
        new_marker = store["11917bf1e75d"].extra[SESSION_EXTRA_DISPATCH_KEY]
        self.assertNotEqual(
            new_marker["job_id"], "1779022229629-ffff03784aaf"
        )


# ---------------------------------------------------------------------------
# case 3 expanded — wrong session / wrong type / terminal row all re-enqueue
# ---------------------------------------------------------------------------


class StaleMarkerVariantsReEnqueueTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        self.worker = CodingExecutorWorker(
            queue=self.queue, heartbeats=self.heartbeats
        )

    def _insert_row(
        self,
        *,
        job_id: str,
        job_type: str,
        session_id: str,
        state: JobState,
    ) -> None:
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES (?, ?, '', ?, '{}', '{}', ?, 0, 0, 3, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id, job_type, session_id, state.value,
                    now_ts, now_ts, now_ts,
                ),
            )
            conn.commit()

    def _drive(
        self, session: _SessionFake
    ) -> tuple:
        store = {session.session_id: session}

        def _update(updated, *, now=None):  # noqa: ANN001
            store[updated.session_id] = _SessionFake(
                session_id=updated.session_id,
                prompt=getattr(updated, "prompt", ""),
                extra=dict(updated.extra),
            )

        dispatched = dispatch_ready_coding_jobs(
            worker=self.worker,
            session_loader=lambda: [store[session.session_id]],
            update_session_fn=_update,
        )
        return dispatched, store

    def test_wrong_type_marker_self_heals(self) -> None:
        self._insert_row(
            job_id="job-wrongtype",
            job_type="approval_post",
            session_id="s-wt",
            state=JobState.QUEUED,
        )
        session = _ready_session(
            "s-wt", marker={"job_id": "job-wrongtype"}
        )
        dispatched, _ = self._drive(session)
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(dispatched[0].created)
        self.assertEqual(
            dispatched[0].stale_marker_reason,
            PHANTOM_MARKER_REASON_WRONG_TYPE,
        )

    def test_wrong_session_marker_self_heals(self) -> None:
        self._insert_row(
            job_id="job-otherses",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="someone-else",
            state=JobState.QUEUED,
        )
        session = _ready_session(
            "s-ws", marker={"job_id": "job-otherses"}
        )
        dispatched, _ = self._drive(session)
        self.assertEqual(len(dispatched), 1)
        self.assertTrue(dispatched[0].created)
        self.assertEqual(
            dispatched[0].stale_marker_reason,
            PHANTOM_MARKER_REASON_WRONG_SESSION,
        )

    def test_terminal_row_marker_is_NOT_self_healed(self) -> None:
        """P1-C — FAILED_TERMINAL row 는 producer 가 절대 새로 enqueue
        하면 안 됨 (dispatch 결과로 인정). 옛 P0-Z 가 stale 로 잘못 분류
        했던 회귀를 핀."""

        self._insert_row(
            job_id="job-dead",
            job_type=JOB_TYPE_CODING_EXECUTE,
            session_id="s-term",
            state=JobState.FAILED_TERMINAL,
        )
        session = _ready_session("s-term", marker={"job_id": "job-dead"})
        dispatched, _ = self._drive(session)
        # 새 row 생성 안 됨
        self.assertEqual(dispatched, ())


if __name__ == "__main__":
    unittest.main()
