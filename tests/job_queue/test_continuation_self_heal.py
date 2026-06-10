"""P0-X — coding-proposal self-heal at the continuation boundary.

Live live-smoke (canonical session ``11917bf1e75d``):
  * issue anchor 까지는 만들어졌고
  * github_work_order row 가 ``SAVED`` 로 마감됐지만
  * ``coding_dispatch_noop_reason = no_coding_proposal`` 로 멈춰 있음.

본 모듈은 5 사용자 요구사항을 모두 stdlib unittest 로 가드:

  1. full-stack request 가 qa-test 로 오분류되지 않는다 (이전 PR 가드의
     보강 — 본 PR 의 새 self-heal 이 의도 외 케이스에 작동하지 않게).
  2. ``promote_session_to_coding_ready`` 가 prompt 만 받으면 coding_proposal
     없어도 자체 재구성 후 promote.
  3. ``repair_stranded_coding_sessions`` 가 SAVED 된 stranded session 을
     scan 후 repair.
  4. repair 후 session 에 coding_job=ready 가 stamp 되어 dispatcher 가
     coding_execute 로 넘길 수 있는 상태.
  5. operator 가 새 intake 없이 runtime restart 만으로 복구 가능.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.job_queue.github_work_order import (
    GitHubWorkOrder,
    JOB_TYPE_GITHUB_WORK_ORDER,
    dispatch_github_work_order,
)
from yule_engineering.agents.job_queue.github_work_order_executor import (
    GitHubWorkOrderWorker,
    SESSION_EXTRA_GITHUB_ISSUE_KEY,
    repair_stranded_coding_sessions,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue
from yule_engineering.agents.job_queue.work_order_coding_continuation import (
    CONTINUATION_NOOP_NO_PROPOSAL,
    SESSION_EXTRA_CODING_JOB_KEY,
    SESSION_EXTRA_CODING_PROPOSAL_KEY,
    promote_session_to_coding_ready,
)


_LIVE_PROMPT = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버 검색 풀스택 MVP 구현해줘. "
    "프론트 / 백엔드 / 데이터베이스 / 도커 / 회원가입 + 검색 + 블로그 + 메일."
)
_REPO = "yule-studio/naver-search-clone"


# ---------------------------------------------------------------------------
# 2. promote_session_to_coding_ready auto-rebuild
# ---------------------------------------------------------------------------


class ContinuationAutoRebuildTests(unittest.TestCase):
    def _anchor(self, issue_number: int = 1) -> Dict[str, Any]:
        return {
            "issue_number": issue_number,
            "created_via": "auto_create",
            "html_url": f"https://github.com/{_REPO}/issues/{issue_number}",
            "approval_id": "approval-1",
            "approved_by": "operator",
            "approved_at": "2026-05-17T11:00:00+00:00",
            "repo": _REPO,
            "dry_run": False,
        }

    def test_rebuilds_missing_proposal_from_prompt(self) -> None:
        outcome = promote_session_to_coding_ready(
            session_extra={},
            anchor=self._anchor(),
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            session_prompt=_LIVE_PROMPT,
            session_id_for_proposal="11917bf1e75d",
        )
        self.assertTrue(outcome.promoted)
        self.assertIsNone(outcome.noop_reason)
        self.assertIsNotNone(outcome.new_extra)
        proposal = outcome.new_extra[SESSION_EXTRA_CODING_PROPOSAL_KEY]
        self.assertIsInstance(proposal, Mapping)
        # marker 가 stamp 되어 stranded → self-heal 경로임을 audit
        self.assertEqual(
            proposal.get("metadata", {}).get("rebuilt_by"),
            "continuation_self_heal",
        )
        coding_job = outcome.new_extra[SESSION_EXTRA_CODING_JOB_KEY]
        self.assertEqual(str(coding_job.get("status") or "").lower(), "ready")
        self.assertEqual(coding_job["metadata"]["issue_number"], 1)

    def test_skips_rebuild_when_auto_rebuild_disabled(self) -> None:
        outcome = promote_session_to_coding_ready(
            session_extra={},
            anchor=self._anchor(),
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            session_prompt=_LIVE_PROMPT,
            auto_rebuild_proposal=False,
        )
        self.assertFalse(outcome.promoted)
        self.assertEqual(outcome.noop_reason, CONTINUATION_NOOP_NO_PROPOSAL)

    def test_skips_rebuild_when_prompt_empty(self) -> None:
        outcome = promote_session_to_coding_ready(
            session_extra={},
            anchor=self._anchor(),
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            session_prompt="",
        )
        self.assertFalse(outcome.promoted)
        self.assertEqual(outcome.noop_reason, CONTINUATION_NOOP_NO_PROPOSAL)

    def test_existing_proposal_takes_precedence_over_rebuild(self) -> None:
        existing_proposal = {
            "session_id": "sess-x",
            "user_request": "preserved",
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead"],
            "participant_roles": ["backend-engineer", "tech-lead"],
            "write_scope": [],
            "forbidden_scope": [],
            "reason": "preserved",
            "safety_rules": [],
            "approval_required": True,
            "metadata": {"preserved_marker": "yes"},
            "lifecycle_mode": "implementation",
            "research_leads": [],
        }
        outcome = promote_session_to_coding_ready(
            session_extra={
                SESSION_EXTRA_CODING_PROPOSAL_KEY: existing_proposal
            },
            anchor=self._anchor(),
            repo=_REPO,
            base_branch="main",
            dry_run=True,
            session_prompt=_LIVE_PROMPT,
        )
        self.assertTrue(outcome.promoted)
        preserved = outcome.new_extra[SESSION_EXTRA_CODING_PROPOSAL_KEY]
        self.assertEqual(
            preserved["metadata"]["preserved_marker"], "yes"
        )


# ---------------------------------------------------------------------------
# 3. repair_stranded_coding_sessions — SAVED rows sweep
# ---------------------------------------------------------------------------


@dataclass
class _SessionFake:
    session_id: str
    prompt: str
    extra: Dict[str, Any] = field(default_factory=dict)


class RepairStrandedCodingSessionsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)

    def _seed_saved_no_proposal_row(
        self,
        *,
        job_id_hint: str,
        session_id: str,
        anchor_issue_number: int,
    ) -> str:
        """Insert a SAVED github_work_order row whose result_json says the
        continuation stalled at ``no_coding_proposal``.
        """

        wo = GitHubWorkOrder(
            proposal_id=f"prop-{job_id_hint}",
            session_id=session_id,
            approval_id=f"approval-{job_id_hint}",
            approved_by="operator",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary=_LIVE_PROMPT,
            repo=_REPO,
            dry_run=False,
            existing_issue_number=anchor_issue_number,
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None
        # Force the row into SAVED + the noop-marker result_json shape we want.
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE job_queue SET state = ?, result_json = ? WHERE job_id = ?",
                (
                    JobState.SAVED.value,
                    json.dumps(
                        {
                            "created_via": "auto_create",
                            "issue_number": anchor_issue_number,
                            "coding_dispatch_queued": False,
                            "coding_dispatch_noop_reason": "no_coding_proposal",
                        }
                    ),
                    job.job_id,
                ),
            )
            conn.commit()
        return job.job_id

    def test_repairs_stranded_session_and_returns_session_id(self) -> None:
        session_id = "11917bf1e75d"
        self._seed_saved_no_proposal_row(
            job_id_hint="x", session_id=session_id, anchor_issue_number=1
        )

        # Session store stub — preload session WITHOUT coding_proposal but
        # WITH anchor (matching live state).
        session = _SessionFake(
            session_id=session_id,
            prompt=_LIVE_PROMPT,
            extra={
                SESSION_EXTRA_GITHUB_ISSUE_KEY: {
                    "issue_number": 1,
                    "repo": _REPO,
                    "created_via": "auto_create",
                    "html_url": f"https://github.com/{_REPO}/issues/1",
                    "approval_id": "approval-x",
                    "approved_by": "operator",
                    "approved_at": "2026-05-17T11:00:00+00:00",
                    "dry_run": False,
                },
            },
        )
        store: Dict[str, _SessionFake] = {session_id: session}

        def _load(sid: str) -> Optional[_SessionFake]:
            return store.get(sid)

        def _update(s: _SessionFake, _new_extra: Mapping[str, Any]) -> None:
            store[s.session_id] = s

        repaired = repair_stranded_coding_sessions(
            self.queue,
            load_session_fn=_load,
            update_session_fn=_update,
        )

        self.assertEqual(repaired, (session_id,))
        repaired_session = store[session_id]
        # 4. coding_job=ready 가 stamp 되어 coding_execute 단계로 갈 수 있다.
        coding_job = repaired_session.extra[SESSION_EXTRA_CODING_JOB_KEY]
        self.assertEqual(str(coding_job.get("status") or "").lower(), "ready")
        self.assertEqual(coding_job["metadata"]["issue_number"], 1)
        # proposal 도 stamp
        self.assertIn(
            SESSION_EXTRA_CODING_PROPOSAL_KEY, repaired_session.extra
        )

    def test_skips_rows_that_already_dispatched(self) -> None:
        """coding_dispatch_queued=True 인 row 는 건드리지 않음."""

        wo = GitHubWorkOrder(
            proposal_id="prop-ok",
            session_id="sess-ok",
            approval_id="approval-ok",
            approved_by="operator",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary="x",
            repo=_REPO,
            existing_issue_number=2,
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE job_queue SET state = ?, result_json = ? WHERE job_id = ?",
                (
                    JobState.SAVED.value,
                    json.dumps(
                        {
                            "coding_dispatch_queued": True,
                            "issue_number": 2,
                        }
                    ),
                    job.job_id,
                ),
            )
            conn.commit()

        store: Dict[str, _SessionFake] = {}
        called: List[str] = []

        def _load(sid):
            called.append(sid)
            return store.get(sid)

        def _update(s, _e):
            store[s.session_id] = s

        repaired = repair_stranded_coding_sessions(
            self.queue, load_session_fn=_load, update_session_fn=_update
        )
        self.assertEqual(repaired, ())
        self.assertEqual(called, [], "skipped row must not load session")

    def test_skips_rows_with_other_noop_reason(self) -> None:
        """다른 noop_reason 은 건드리지 않음 (already_ready 등)."""

        wo = GitHubWorkOrder(
            proposal_id="prop-other",
            session_id="sess-other",
            approval_id="approval-other",
            approved_by="operator",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary="x",
            repo=_REPO,
            existing_issue_number=3,
        )
        job = dispatch_github_work_order(self.queue, wo).job
        assert job is not None
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE job_queue SET state = ?, result_json = ? WHERE job_id = ?",
                (
                    JobState.SAVED.value,
                    json.dumps(
                        {
                            "coding_dispatch_queued": False,
                            "coding_dispatch_noop_reason": "coding_job_already_ready_same_anchor",
                        }
                    ),
                    job.job_id,
                ),
            )
            conn.commit()

        repaired = repair_stranded_coding_sessions(
            self.queue,
            load_session_fn=lambda _sid: None,
            update_session_fn=lambda _s, _e: None,
        )
        self.assertEqual(repaired, ())

    def test_idempotent_when_called_twice(self) -> None:
        """두 번째 호출은 이미 ready 인 session 을 다시 promote 하지 않음."""

        session_id = "sess-twice"
        self._seed_saved_no_proposal_row(
            job_id_hint="twice", session_id=session_id, anchor_issue_number=7
        )

        session = _SessionFake(
            session_id=session_id,
            prompt=_LIVE_PROMPT,
            extra={
                SESSION_EXTRA_GITHUB_ISSUE_KEY: {
                    "issue_number": 7,
                    "repo": _REPO,
                    "created_via": "auto_create",
                    "approval_id": "approval-x",
                    "approved_by": "operator",
                    "approved_at": "2026-05-17T11:00:00+00:00",
                    "dry_run": False,
                },
            },
        )
        store = {session_id: session}

        def _load(sid):
            return store.get(sid)

        def _update(s, _e):
            store[s.session_id] = s

        first = repair_stranded_coding_sessions(
            self.queue, load_session_fn=_load, update_session_fn=_update
        )
        second = repair_stranded_coding_sessions(
            self.queue, load_session_fn=_load, update_session_fn=_update
        )

        self.assertEqual(first, (session_id,))
        # 두 번째 호출: row 의 result_json 자체는 아직 noop 인 채로 남아
        # 있어 sweep 대상이지만, repair_session_for_coding_dispatch 가
        # already_ready noop 으로 반환 → sweep 결과는 빈 tuple.
        self.assertEqual(second, ())


# ---------------------------------------------------------------------------
# 5. End-to-end self-heal via worker (next run picks live row)
# ---------------------------------------------------------------------------


class WorkerInlineSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)
        # Session fakes — keyed by id for the worker injection
        self.sessions: Dict[str, _SessionFake] = {}

        def _load(sid):
            return self.sessions.get(sid)

        def _update(s, new_extra):
            try:
                s.extra = dict(new_extra)
            except Exception:  # noqa: BLE001
                pass
            self.sessions[s.session_id] = s
            return s

        self._load = _load
        self._update = _update

    def test_worker_processes_existing_anchor_then_self_heals_continuation(
        self,
    ) -> None:
        """anchor 만 들어있고 proposal 비어있는 session → worker 가
        existing_issue branch 로 흘러가서 continuation 단계에서 self-heal."""

        session_id = "sess-end-to-end"
        self.sessions[session_id] = _SessionFake(
            session_id=session_id,
            prompt=_LIVE_PROMPT,
            extra={},  # 의도적으로 proposal 없음
        )

        # writer 는 없어도 됨 — existing_issue branch 는 writer 미사용
        worker = GitHubWorkOrderWorker(
            queue=self.queue,
            writer_factory=lambda _wo: (None, "L2"),
            heartbeats=self.heartbeats,
            load_session_fn=self._load,
            update_session_fn=self._update,
        )
        wo = GitHubWorkOrder(
            proposal_id="prop-end-to-end",
            session_id=session_id,
            approval_id="approval-end-to-end",
            approved_by="operator",
            approved_at="2026-05-17T11:00:00+00:00",
            request_summary=_LIVE_PROMPT,
            repo=_REPO,
            existing_issue_number=42,  # existing anchor branch
        )
        dispatch_github_work_order(self.queue, wo)

        outcome = worker.run_one()
        assert outcome is not None
        # continuation 이 self-heal 으로 promote 됐어야 함
        self.assertTrue(
            outcome.audit_summary.get("continuation_promoted"),
            f"continuation did not promote; audit={outcome.audit_summary}",
        )
        # session.extra 가 coding_job=ready 까지 도달
        session_after = self.sessions[session_id]
        coding_job = session_after.extra.get(SESSION_EXTRA_CODING_JOB_KEY)
        self.assertIsNotNone(coding_job)
        self.assertEqual(str(coding_job.get("status") or "").lower(), "ready")


if __name__ == "__main__":
    unittest.main()
