"""P1-I — auto-revive ``coding_execute`` rows blocked at
``bootstrap_required:*`` after operator enables greenfield bootstrap.

Canonical session ``11917bf1e75d`` 의 latest terminal rows (e.g.
``1779058744308-e4ccbe2d5c4a``) carry
``result.reason = bootstrap_required:no_stack_detected+editor_record_only_insufficient``.
Once operator sets ``YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED=1``
AND target repo checkout exists, the recovery hook revives those rows
without requiring a new intake.

사용자가 명시한 8 케이스 모두 stdlib unittest 가드.
"""

from __future__ import annotations

import json
import os
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


from yule_orchestrator.agents.job_queue.coding_execute_recovery import (
    BOOTSTRAP_RECOVERABLE_SUB_TOKENS,
    BOOTSTRAP_REQUIRED_REASON_PREFIX,
    _classify_bootstrap_reason,
    _is_bootstrap_capability_enabled,
    recover_bootstrap_required_rows,
    recover_target_repo_missing_rows,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    ENV_GREENFIELD_BOOTSTRAP_ENABLED,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    REASON_BOOTSTRAP_REQUIRED,
    REASON_TARGET_REPO_MISSING,
)
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"


class _EnvScope:
    """Context manager that sets env vars within block; restores on exit."""

    def __init__(self, **values: Optional[str]) -> None:
        self._values = values
        self._original: Dict[str, Optional[str]] = {}

    def __enter__(self) -> "_EnvScope":
        for key, value in self._values.items():
            self._original[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _insert_coding_row(
    db_path: Path,
    *,
    job_id: str,
    session_id: str,
    state: JobState,
    payload: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    attempt: int = 0,
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
            VALUES (?, 'coding_execute', '', ?, ?, ?, ?, 0, ?, 3, ?,
                    NULL, NULL, ?, ?)
            """,
            (
                job_id,
                session_id,
                json.dumps(dict(payload or {"repo_full_name": _REPO})),
                json.dumps(dict(result or {})),
                state.value,
                attempt,
                now_ts,
                now_ts,
                now_ts,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Classifier truth table — recoverable subset
# ---------------------------------------------------------------------------


class ClassifierTests(unittest.TestCase):
    def test_record_only_insufficient_is_recoverable(self) -> None:
        sub = _classify_bootstrap_reason(
            "bootstrap_required:no_stack_detected+editor_record_only_insufficient"
        )
        self.assertEqual(sub, "editor_record_only_insufficient")

    def test_live_editor_unavailable_is_recoverable(self) -> None:
        sub = _classify_bootstrap_reason(
            "bootstrap_required:live_editor_unavailable:greenfield_full_stack"
        )
        self.assertEqual(sub, "live_editor_unavailable")

    def test_scaffold_apply_failed_is_not_recoverable(self) -> None:
        sub = _classify_bootstrap_reason(
            "bootstrap_required:scaffold_apply_failed:greenfield_full_stack"
        )
        self.assertIsNone(sub)

    def test_unrelated_reason_is_not_recoverable(self) -> None:
        sub = _classify_bootstrap_reason("test_failed")
        self.assertIsNone(sub)


# ---------------------------------------------------------------------------
# Recovery sweep — gate semantics + revive semantics
# ---------------------------------------------------------------------------


class RecoverySweepTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.repo_dir = Path(self.repo_tmp.name) / "naver-search-clone"
        self.repo_dir.mkdir(parents=True)

    def _seed_blocked(
        self,
        *,
        job_id: str,
        session_id: str,
        state: JobState = JobState.FAILED_TERMINAL,
        reason: str = "bootstrap_required:no_stack_detected+editor_record_only_insufficient",
    ) -> None:
        _insert_coding_row(
            self.db_path,
            job_id=job_id,
            session_id=session_id,
            state=state,
            payload={"repo_full_name": _REPO, "session_id": session_id},
            result={
                "reason": reason,
                "branch": "agent/backend-engineer/issue-1-coding-execute",
            },
        )

    def test_env_off_no_revive(self) -> None:
        """case 1 — env opt-in 안 됐으면 revive 0건."""

        self._seed_blocked(
            job_id="1779058744308-e4ccbe2d5c4a", session_id="11917bf1e75d"
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: None}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertEqual(revived, ())
        row = self.queue.get("1779058744308-e4ccbe2d5c4a")
        self.assertEqual(row.state, JobState.FAILED_TERMINAL)

    def test_env_on_repo_exists_revive_failed_terminal(self) -> None:
        """case 2 — capability + repo 모두 OK → failed_terminal → queued."""

        self._seed_blocked(
            job_id="1779058744308-e4ccbe2d5c4a", session_id="11917bf1e75d"
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertEqual(revived, ("1779058744308-e4ccbe2d5c4a",))
        row = self.queue.get("1779058744308-e4ccbe2d5c4a")
        self.assertEqual(row.state, JobState.QUEUED)
        self.assertEqual(row.attempt, 0)
        # case 6: revivals audit appended
        self.assertIn("revivals", row.result)
        last = row.result["revivals"][-1]
        self.assertEqual(last["reason"], "bootstrap_capability_enabled")
        self.assertEqual(
            last["trigger_sub_token"], "editor_record_only_insufficient"
        )
        self.assertEqual(last["editor"], "GreenfieldBootstrapEditor")
        self.assertEqual(
            last["env"], "YULE_CODING_EXECUTOR_GREENFIELD_BOOTSTRAP_ENABLED"
        )
        self.assertEqual(last["repo_full_name"], _REPO)

    def test_live_editor_unavailable_path_also_recovers(self) -> None:
        """case 3 — alternative recoverable sub-token."""

        self._seed_blocked(
            job_id="job-live-editor",
            session_id="sess-live",
            reason="bootstrap_required:live_editor_unavailable:greenfield_full_stack",
            state=JobState.FAILED_TERMINAL,
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertIn("job-live-editor", revived)
        row = self.queue.get("job-live-editor")
        self.assertEqual(row.state, JobState.QUEUED)
        self.assertEqual(
            row.result["revivals"][-1]["trigger_sub_token"],
            "live_editor_unavailable",
        )

    def test_unrelated_terminal_reason_is_not_revived(self) -> None:
        """case 4 — ``test_failed`` 등 다른 reason 의 failed_terminal 은
        절대 건드리지 않음."""

        self._seed_blocked(
            job_id="job-other",
            session_id="sess-other",
            reason="test_failed",
            state=JobState.FAILED_TERMINAL,
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertEqual(revived, ())
        row = self.queue.get("job-other")
        self.assertEqual(row.state, JobState.FAILED_TERMINAL)

    def test_scaffold_apply_failed_is_not_revived(self) -> None:
        """``scaffold_apply_failed`` sub-token 은 운영자 intervention 이
        필요 — recovery 가 자동 revive 하지 않는다."""

        self._seed_blocked(
            job_id="job-scaffold-fail",
            session_id="sess-sf",
            reason="bootstrap_required:scaffold_apply_failed:greenfield_full_stack",
            state=JobState.FAILED_TERMINAL,
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertEqual(revived, ())

    def test_repeated_sweeps_do_not_churn(self) -> None:
        """case 5 — 두 번째 sweep 이 ``QUEUED`` 가 된 row 를 다시
        revive 하지 않음 (queue 는 failed_* 만 picks 한다)."""

        self._seed_blocked(
            job_id="1779058744308-e4ccbe2d5c4a", session_id="11917bf1e75d"
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            first = recover_bootstrap_required_rows(
                self.queue, repo_resolver=lambda _n: str(self.repo_dir)
            )
            second = recover_bootstrap_required_rows(
                self.queue, repo_resolver=lambda _n: str(self.repo_dir)
            )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, ())

    def test_missing_target_repo_skips_revive(self) -> None:
        """env on 이라도 repo checkout 이 없으면 skip — target_repo
        recovery hook 이 먼저 materialize 해야 한다."""

        self._seed_blocked(
            job_id="job-no-repo", session_id="sess-no-repo"
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: None,  # checkout missing
            )
        self.assertEqual(revived, ())
        row = self.queue.get("job-no-repo")
        self.assertEqual(row.state, JobState.FAILED_TERMINAL)


# ---------------------------------------------------------------------------
# Case 8 — target-repo and bootstrap recovery coexist
# ---------------------------------------------------------------------------


class CoexistenceWithTargetRepoRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.repo_dir = Path(self.repo_tmp.name) / "naver-search-clone"
        self.repo_dir.mkdir(parents=True)

    def test_both_hooks_only_touch_their_own_rows(self) -> None:
        """case 8 — target_repo + bootstrap recovery 가 같은 sweep 에서
        호출돼도 각자 자기 reason 의 row 만 revive."""

        # target_repo_missing failed_retryable
        _insert_coding_row(
            self.db_path,
            job_id="job-target-repo",
            session_id="sess-tr",
            state=JobState.FAILED_RETRYABLE,
            payload={"repo_full_name": _REPO, "session_id": "sess-tr"},
            result={
                "reason": "target_repo_checkout_missing: TargetRepoUnavailableError: ...",
            },
        )
        # bootstrap_required failed_terminal
        _insert_coding_row(
            self.db_path,
            job_id="job-bootstrap",
            session_id="sess-bs",
            state=JobState.FAILED_TERMINAL,
            payload={"repo_full_name": _REPO, "session_id": "sess-bs"},
            result={
                "reason": "bootstrap_required:no_stack_detected+editor_record_only_insufficient",
            },
        )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            target = recover_target_repo_missing_rows(
                self.queue, repo_resolver=lambda _n: str(self.repo_dir)
            )
            boot = recover_bootstrap_required_rows(
                self.queue, repo_resolver=lambda _n: str(self.repo_dir)
            )

        self.assertEqual(target, ("job-target-repo",))
        self.assertEqual(boot, ("job-bootstrap",))
        # 각각 정상적으로 QUEUED 로 도달
        self.assertEqual(self.queue.get("job-target-repo").state, JobState.QUEUED)
        self.assertEqual(self.queue.get("job-bootstrap").state, JobState.QUEUED)


# ---------------------------------------------------------------------------
# Case 7 — canonical session shape regression
# ---------------------------------------------------------------------------


class CanonicalSessionShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.repo_dir = Path(self.repo_tmp.name) / "naver-search-clone"
        self.repo_dir.mkdir()

    def test_canonical_three_rows_all_revived_after_opt_in(self) -> None:
        """case 7 — live 의 3 failed_terminal row shape 시뮬:
        ``1779058744308-…``, ``1779058734281-…``, ``1779058012948-…``
        — env opt-in + checkout 존재 → 셋 다 revive."""

        for job_id, session_id in (
            ("1779058744308-e4ccbe2d5c4a", "11917bf1e75d"),
            ("1779058734281-930d390b0972", "11917bf1e75d"),
            ("1779058012948-845d7662e5f1", "11917bf1e75d"),
        ):
            _insert_coding_row(
                self.db_path,
                job_id=job_id,
                session_id=session_id,
                state=JobState.FAILED_TERMINAL,
                payload={"repo_full_name": _REPO, "session_id": session_id},
                result={
                    "reason": (
                        "bootstrap_required:no_stack_detected"
                        "+editor_record_only_insufficient"
                    ),
                    "branch": "agent/backend-engineer/issue-1-coding-execute",
                },
            )
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            revived = recover_bootstrap_required_rows(
                self.queue,
                repo_resolver=lambda _n: str(self.repo_dir),
            )
        self.assertEqual(sorted(revived), [
            "1779058012948-845d7662e5f1",
            "1779058734281-930d390b0972",
            "1779058744308-e4ccbe2d5c4a",
        ])
        for jid in revived:
            self.assertEqual(self.queue.get(jid).state, JobState.QUEUED)


if __name__ == "__main__":
    unittest.main()
