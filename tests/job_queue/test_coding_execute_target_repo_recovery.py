"""P1-D — target_repo_checkout_missing 가 환경 복구 후 자동 revive 되는지.

라이브 canonical session ``11917bf1e75d`` 의 latest failed_terminal job
``1779027211596-9215f2cf937b`` 의 result_json:

  {
    "reason": "target_repo_checkout_missing: TargetRepoUnavailableError: ...",
    ...
  }

이 상태는 operator 가 ``/Users/masterway/local-dev/naver-search-clone``
checkout 을 만들거나 ``YULE_CODING_EXECUTOR_REPO_ROOTS_JSON`` 환경을
설정해 주면 *자동으로* 다시 시도되어야 한다.

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드:

  1. target repo missing failure 는 recoverable (terminal=False) 로
     classification 됨 (옛 terminal=True 회귀 차단)
  2. checkout 없을 때 recovery hook 는 skip — queue churn 없음
  3. checkout 생기면 recovery hook 가 failed_retryable 또는
     failed_terminal row 를 큐로 revive (canonical scenario)
  4. revive 후 attempt 카운터 / available_at 가 reasonable
  5. dispatcher 는 unrelated terminal row (다른 reason) 는 그대로 둠
  6. canonical-style row shape (``1779027211596-…`` 모양) end-to-end
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.job_queue.coding_execute_recovery import (
    TARGET_REPO_MISSING_REASON_PREFIX,
    _revive_failed_terminal_row,
    recover_target_repo_missing_rows,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    REASON_TARGET_REPO_MISSING,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"


def _insert_coding_row(
    db_path: Path,
    *,
    job_id: str,
    session_id: str,
    state: JobState,
    payload: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    attempt: int = 0,
    max_attempts: int = 3,
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
            VALUES (?, 'coding_execute', '', ?, ?, ?, ?, 0, ?, ?, ?,
                    NULL, NULL, ?, ?)
            """,
            (
                job_id,
                session_id,
                json.dumps(dict(payload or {"repo_full_name": _REPO})),
                json.dumps(dict(result or {})),
                state.value,
                attempt,
                max_attempts,
                now_ts,
                now_ts,
                now_ts,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Case 1 — classification: target_repo_checkout_missing → recoverable
# ---------------------------------------------------------------------------


class ClassificationIsRecoverableTests(unittest.TestCase):
    def test_classification_via_source_grep(self) -> None:
        """Functional fallback — source-level guard: the ``target_repo_unavail``
        branch must set ``terminal = False``."""

        from yule_engineering.agents.job_queue import coding_executor_worker as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        # find the line where target_repo_unavail branch sets terminal
        # (the only assignment of terminal next to that conditional in
        # the file). pattern must be present.
        self.assertIn("if target_repo_unavail:", source)
        # rough check — the branch ends with terminal = False, not True.
        # narrow to the snippet between the conditional and the next
        # ``elif worktree_specific``.
        start = source.find("if target_repo_unavail:")
        end = source.find("elif worktree_specific:", start)
        self.assertGreater(end, start)
        snippet = source[start:end]
        self.assertIn("terminal = False", snippet)
        self.assertNotIn("terminal = True", snippet)


# ---------------------------------------------------------------------------
# Case 2 + 3 — recovery hook: skip when missing, revive when present
# ---------------------------------------------------------------------------


class RecoveryHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.repo_dir = Path(self.repo_tmp.name) / "naver-search-clone"

    def _make_blocked_row(
        self,
        *,
        job_id: str,
        session_id: str,
        state: JobState = JobState.FAILED_RETRYABLE,
    ) -> None:
        _insert_coding_row(
            self.db_path,
            job_id=job_id,
            session_id=session_id,
            state=state,
            payload={"repo_full_name": _REPO, "session_id": session_id},
            result={
                "reason": (
                    f"{REASON_TARGET_REPO_MISSING}: TargetRepoUnavailableError: "
                    f"target repo {_REPO!r} not found"
                ),
                "branch": "agent/backend-engineer/issue-1-coding-execute",
            },
        )

    def test_skip_when_checkout_still_missing(self) -> None:
        """case 2 — repo 없으면 revive 0건 + 행은 그대로."""

        self._make_blocked_row(
            job_id="job-still-missing", session_id="sess-x"
        )
        # resolver injection: 항상 None — 체크아웃 미생성 시뮬
        revived = recover_target_repo_missing_rows(
            self.queue, repo_resolver=lambda _name: None
        )
        self.assertEqual(revived, ())
        row = self.queue.get("job-still-missing")
        self.assertEqual(row.state, JobState.FAILED_RETRYABLE)

    def test_revive_failed_retryable_when_checkout_appears(self) -> None:
        """case 3 — repo 생기면 failed_retryable row 를 queued 로 revive."""

        self._make_blocked_row(
            job_id="job-retry-revive", session_id="sess-r"
        )
        self.repo_dir.mkdir(parents=True)
        revived = recover_target_repo_missing_rows(
            self.queue, repo_resolver=lambda _name: str(self.repo_dir)
        )
        self.assertEqual(len(revived), 1)
        self.assertEqual(revived[0], "job-retry-revive")
        row = self.queue.get("job-retry-revive")
        self.assertEqual(row.state, JobState.QUEUED)

    def test_revive_failed_terminal_when_checkout_appears(self) -> None:
        """case 3 + 6 — canonical session 의 failed_terminal row 도
        직접 SQL revive 로 queued 복귀."""

        self._make_blocked_row(
            job_id="1779027211596-9215f2cf937b",
            session_id="11917bf1e75d",
            state=JobState.FAILED_TERMINAL,
        )
        self.repo_dir.mkdir(parents=True)
        revived = recover_target_repo_missing_rows(
            self.queue, repo_resolver=lambda _name: str(self.repo_dir)
        )
        self.assertEqual(len(revived), 1)
        self.assertIn("1779027211596-9215f2cf937b", revived)
        row = self.queue.get("1779027211596-9215f2cf937b")
        self.assertEqual(row.state, JobState.QUEUED)
        # case 4: attempt 카운터는 0 으로 리셋 (queue retry semantics
        # 가 다시 max_attempts 까지 시도 가능)
        self.assertEqual(row.attempt, 0)
        # audit revival 흔적이 result_json 에 남음
        self.assertIn("revivals", row.result)


# ---------------------------------------------------------------------------
# Case 5 — recovery does NOT touch unrelated terminal rows
# ---------------------------------------------------------------------------


class RecoveryNotOverreachTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_recovery_skips_rows_with_unrelated_reason(self) -> None:
        """case 5 — 다른 error reason (executor_failed 등) 은 recovery
        대상이 아님. 옛 P0-Z 의 overreach 패턴이 본 hook 에서 재발하지
        않게 회귀 차단."""

        _insert_coding_row(
            self.db_path,
            job_id="job-other-error",
            session_id="sess-other",
            state=JobState.FAILED_TERMINAL,
            result={"reason": "test_failed", "branch": "agent/x"},
        )
        revived = recover_target_repo_missing_rows(
            self.queue,
            repo_resolver=lambda _n: "/tmp/anything",  # resolver returns path
        )
        self.assertEqual(revived, ())
        row = self.queue.get("job-other-error")
        self.assertEqual(row.state, JobState.FAILED_TERMINAL)


# ---------------------------------------------------------------------------
# Direct revive helper unit tests
# ---------------------------------------------------------------------------


class ReviveTerminalHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)

    def test_revive_returns_false_for_non_terminal_row(self) -> None:
        """state 가 이미 다른 곳으로 옮겨졌으면 (race) revive 거부."""

        _insert_coding_row(
            self.db_path,
            job_id="job-q",
            session_id="s",
            state=JobState.QUEUED,
        )
        ok = _revive_failed_terminal_row(
            db_path=self.db_path, job_id="job-q", note={}
        )
        self.assertFalse(ok)

    def test_revive_clears_lease_and_resets_attempt(self) -> None:
        """revive 결과: state=queued, attempt=0, picked_by/picked_until null."""

        _insert_coding_row(
            self.db_path,
            job_id="job-t",
            session_id="s",
            state=JobState.FAILED_TERMINAL,
            attempt=3,
        )
        ok = _revive_failed_terminal_row(
            db_path=self.db_path,
            job_id="job-t",
            note={"reason": "test_revival"},
        )
        self.assertTrue(ok)
        row = self.queue.get("job-t")
        self.assertEqual(row.state, JobState.QUEUED)
        self.assertEqual(row.attempt, 0)
        self.assertIsNone(row.picked_by)
        self.assertIsNone(row.picked_until)


# ---------------------------------------------------------------------------
# Default resolver integration — no explicit resolver injection
# ---------------------------------------------------------------------------


class DefaultResolverIntegrationTests(unittest.TestCase):
    """본 sweep 은 resolver 가 안 주입돼도 default cross-repo resolver
    를 호출해서 작동해야 한다 (production path)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)

    def test_default_resolver_finds_sibling_after_creation(self) -> None:
        """sibling checkout 이 생기면 default resolver 가 발견 + revive."""

        import os

        parent = Path(self.workspace.name)
        orch = parent / "orchestrator"
        orch.mkdir()
        os.environ["YULE_CODING_EXECUTOR_REPO_ROOT"] = str(orch)
        self.addCleanup(
            lambda: os.environ.pop("YULE_CODING_EXECUTOR_REPO_ROOT", None)
        )
        _insert_coding_row(
            self.db_path,
            job_id="job-default-resolver",
            session_id="sess-default",
            state=JobState.FAILED_RETRYABLE,
            payload={"repo_full_name": _REPO, "session_id": "sess-default"},
            result={
                "reason": (
                    f"{REASON_TARGET_REPO_MISSING}: TargetRepoUnavailableError"
                ),
            },
        )
        # 아직 checkout 없음 → no revive
        self.assertEqual(
            recover_target_repo_missing_rows(self.queue), ()
        )
        # 이제 sibling checkout 생성
        sibling = parent / "naver-search-clone"
        sibling.mkdir()
        revived = recover_target_repo_missing_rows(self.queue)
        self.assertEqual(revived, ("job-default-resolver",))


if __name__ == "__main__":
    unittest.main()
