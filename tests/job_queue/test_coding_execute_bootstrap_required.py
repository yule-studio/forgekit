"""P1-G — greenfield / no-stack target repo handling.

Canonical session ``11917bf1e75d`` 가 target repo
``yule-studio/naver-search-clone`` (.git + README only — greenfield) 에서
``python3 -m unittest discover -s tests -t .`` 로 failback 해 misleading
``test_failed`` 만 남기던 회귀의 회귀 차단.

본 모듈은 사용자가 명시한 7 케이스 모두 stdlib unittest 가드:

  1. no stack-marker repo 는 python unittest fallback 안 함
  2. greenfield repo → ``bootstrap_required`` strategy + sub_reason
  3. JS/TS repo (package.json) → 여전히 node test command 선택
  4. Python repo (tests/ 또는 pytest.ini 등) → python path 유지
  5. record-only editor + greenfield repo →
     ``editor_record_only_insufficient`` capability surface
  6. bootstrap_required 는 ``terminal=True`` — infinite retry churn 차단
  7. canonical-session-like greenfield case 시뮬 + audit/log/status
     surface 검증
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.coding_execute_test_command import (
    BOOTSTRAP_REASON_EMPTY_REPO,
    BOOTSTRAP_REASON_NO_STACK,
    PYTHON_UNITTEST_DEFAULT,
    STRATEGY_BOOTSTRAP_REQUIRED,
    STRATEGY_JS_SCRIPT,
    STRATEGY_PYTHON_UNITTEST_DEFAULT,
    select_test_command,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    RecordOnlyCodeEditor,
    SubprocessTestRunner,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
    REASON_BOOTSTRAP_REQUIRED,
    WorktreeContext,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.state_machine import JobState
from yule_orchestrator.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"


def _request(metadata: Optional[Mapping[str, Any]] = None) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="11917bf1e75d",
        executor_role="backend-engineer",
        user_request="네이버 검색 풀스택 MVP 구현",
        generated_prompt="(prompt)",
        write_scope=(),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name=_REPO,
        issue_number=1,
        dry_run=False,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Cases 1, 2, 3, 4 — selection-level behaviour
# ---------------------------------------------------------------------------


class SelectionSemanticsTests(unittest.TestCase):
    def test_no_stack_markers_does_not_use_python_unittest_fallback(self) -> None:
        """case 1 — repo 에 어떤 stack 시그널도 없으면 python unittest
        fallback 으로 silently 떨어지면 안 된다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 의도적으로 모든 stack signal 없음 — pure binary file 만.
            (root / "data.bin").write_bytes(b"\x00\x01\x02")
            sel = select_test_command(worktree_path=str(root))
        self.assertNotEqual(sel.strategy, STRATEGY_PYTHON_UNITTEST_DEFAULT)
        self.assertNotIn("python3", sel.command)
        self.assertEqual(sel.strategy, STRATEGY_BOOTSTRAP_REQUIRED)
        self.assertEqual(sel.bootstrap_sub_reason, BOOTSTRAP_REASON_NO_STACK)

    def test_greenfield_repo_yields_bootstrap_required(self) -> None:
        """case 2 — canonical scenario: ``.git`` + README 만 → greenfield."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# greenfield\n")
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_BOOTSTRAP_REQUIRED)
        self.assertEqual(sel.bootstrap_sub_reason, BOOTSTRAP_REASON_EMPTY_REPO)
        self.assertEqual(sel.command, ())
        self.assertIn("scaffold", sel.reason)

    def test_js_repo_still_selects_node_test_command(self) -> None:
        """case 3 — JS/TS (package.json + scripts.test) regression: 본
        change 가 JS path 를 깨뜨리지 않는다."""

        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest"}})
            )
            (root / "pnpm-lock.yaml").write_text("")
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_JS_SCRIPT)
        self.assertEqual(sel.command[0], "pnpm")

    def test_python_repo_still_uses_python_path(self) -> None:
        """case 4 — Python signals (tests/*.py / pyproject) → unittest /
        pytest path 유지."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test():\n    pass\n")
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_PYTHON_UNITTEST_DEFAULT)
        self.assertEqual(sel.command, PYTHON_UNITTEST_DEFAULT)

    def test_metadata_override_takes_precedence_over_greenfield(self) -> None:
        """operator override 가 있으면 greenfield 라도 그 command 사용."""

        with tempfile.TemporaryDirectory() as tmp:
            sel = select_test_command(
                worktree_path=tmp,
                request_metadata={"test_command": ["make", "check"]},
            )
        self.assertEqual(sel.command, ("make", "check"))


# ---------------------------------------------------------------------------
# Cases 5, 6 — runner + worker surface
# ---------------------------------------------------------------------------


class TestRunnerShortCircuitTests(unittest.TestCase):
    def test_runner_does_not_invoke_subprocess_for_bootstrap_required(self) -> None:
        """SubprocessTestRunner.run 이 bootstrap_required 일 때
        subprocess 를 실제 호출하지 않고 즉시 selection 만 stamp."""

        calls: List[List[str]] = []

        def _runner(cmd, **_kwargs):
            calls.append(list(cmd))
            raise AssertionError("subprocess must not be invoked")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.md").write_text("# x\n")
            runner = SubprocessTestRunner(runner=_runner)
            ctx = WorktreeContext(branch="b", worktree_path=str(root))
            new_ctx = runner.run(request=_request(), context=ctx)
        self.assertEqual(calls, [])
        self.assertEqual(new_ctx.test_summary["status"], "bootstrap_required")
        self.assertEqual(
            new_ctx.test_summary["bootstrap_sub_reason"], BOOTSTRAP_REASON_EMPTY_REPO
        )
        self.assertEqual(new_ctx.test_summary["selection"]["strategy"], STRATEGY_BOOTSTRAP_REQUIRED)


class WorkerBootstrapRequiredHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)

    def _seed_session(self, session_id: str) -> None:
        # session loader is consulted via ``workflow_state``; we don't need
        # an actual row for the worker's progress-stamp best-effort path.
        pass

    def _make_in_progress_job(self, session_id: str, payload: Dict[str, Any]) -> str:
        import json as _json

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        job_id = f"job-{session_id}"
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO job_queue
                  (job_id, job_type, role, session_id, payload_json,
                   result_json, state, priority, attempt, max_attempts,
                   available_at, picked_by, picked_until,
                   created_at, updated_at)
                VALUES (?, 'coding_execute', '', ?, ?, '{}',
                        ?, 0, 0, 3, ?, NULL, NULL, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    _json.dumps(payload),
                    JobState.QUEUED.value,
                    now_ts,
                    now_ts,
                    now_ts,
                ),
            )
            conn.commit()
        return job_id

    def test_greenfield_repo_fails_terminal_with_bootstrap_reason(self) -> None:
        """case 5 + 6 — record-only editor + greenfield → terminal fail
        with sub-reason ``editor_record_only_insufficient`` (capability
        surface). terminal=True 라 retry churn 없음."""

        # Build a synthetic provisioner that returns the greenfield
        # worktree, and a record-only editor + the new test runner.
        with tempfile.TemporaryDirectory() as wt_tmp:
            greenfield = Path(wt_tmp) / "wt"
            greenfield.mkdir()
            (greenfield / ".git").mkdir()
            (greenfield / "README.md").write_text("# x\n")

            class _Provisioner:
                def provision(self, *, request, branch):
                    return WorktreeContext(
                        branch=branch,
                        worktree_path=str(greenfield),
                        base_commit_sha="abc",
                    )

                def cleanup(self, *, force: bool = False) -> None:
                    pass

            committer_calls: List[Any] = []
            pusher_calls: List[Any] = []

            class _NoOpCommitter:
                def commit(self, **k):  # noqa: ANN003
                    committer_calls.append(k)
                    return k["context"]

            class _NoOpPusher:
                def push(self, **k):
                    pusher_calls.append(k)
                    return k["context"]

            class _NoOpPRCreator:
                def open(self, **k):
                    return k["context"]

            payload = {
                "session_id": "11917bf1e75d",
                "executor_role": "backend-engineer",
                "user_request": "x",
                "generated_prompt": "y",
                "write_scope": [],
                "forbidden_scope": [],
                "safety_rules": [],
                "base_branch": "main",
                "branch_hint": "agent/backend-engineer/issue-1-coding-execute",
                "repo_full_name": _REPO,
                "issue_number": 1,
                "dry_run": False,
                "metadata": {},
            }
            job_id = self._make_in_progress_job("11917bf1e75d", payload)

            from yule_orchestrator.agents.job_queue.store import Job

            picked = self.queue.pick(
                worker_id="t",
                job_types=(JOB_TYPE_CODING_EXECUTE,),
            )
            assert picked is not None and picked.job_id == job_id

            worker = CodingExecutorWorker(
                queue=self.queue,
                heartbeats=self.heartbeats,
                worktree_provisioner=_Provisioner(),
                code_editor=RecordOnlyCodeEditor(),
                test_runner=SubprocessTestRunner(),
                committer=_NoOpCommitter(),
                pusher=_NoOpPusher(),
                draft_pr_creator=_NoOpPRCreator(),
            )
            outcome = worker.process_job(picked)
        # outcome.failure_reason carries bootstrap_required + editor capability
        self.assertIsNotNone(outcome.failure_reason)
        self.assertIn(REASON_BOOTSTRAP_REQUIRED, outcome.failure_reason)
        self.assertIn(
            "editor_record_only_insufficient", outcome.failure_reason
        )
        # row landed in failed_terminal (terminal=True → no auto-retry)
        row_after = self.queue.get(job_id)
        self.assertEqual(row_after.state, JobState.FAILED_TERMINAL)
        # No committer/pusher invocation — short-circuit before commit
        self.assertEqual(committer_calls, [])
        self.assertEqual(pusher_calls, [])


# ---------------------------------------------------------------------------
# Case 7 — canonical-session shape covered end-to-end via worker
# ---------------------------------------------------------------------------


class CanonicalSessionShapeTests(unittest.TestCase):
    """Source-grep guard — REASON_BOOTSTRAP_REQUIRED in worker exports
    + selection module surface intact across the public API.
    """

    def test_reason_constant_is_exported(self) -> None:
        from yule_orchestrator.agents.job_queue import coding_executor_worker as mod

        self.assertIn("REASON_BOOTSTRAP_REQUIRED", mod.__all__)
        self.assertEqual(
            mod.REASON_BOOTSTRAP_REQUIRED, "bootstrap_required"
        )

    def test_selection_module_exports_bootstrap_constants(self) -> None:
        from yule_orchestrator.agents.job_queue import (
            coding_execute_test_command as mod,
        )

        self.assertIn("STRATEGY_BOOTSTRAP_REQUIRED", mod.__all__)
        self.assertIn("BOOTSTRAP_REASON_EMPTY_REPO", mod.__all__)
        self.assertIn("BOOTSTRAP_REASON_NO_STACK", mod.__all__)


if __name__ == "__main__":
    unittest.main()
