"""P1-H — greenfield repo bootstrap (planner + applier + editor + worker
mapping).

Canonical session ``11917bf1e75d`` 의 latest fail reason 은
``bootstrap_required:no_stack_detected+editor_record_only_insufficient``.
이번 PR 은 그 capability gap 을 실제로 닫는다:

  * detect_bootstrap_mode — repo 가 greenfield + request 가 full-stack
    이면 ``greenfield_full_stack`` 모드 선택
  * plan_greenfield_scaffold — Next/Nest/Postgres docker-compose minimal
    scaffold deterministic plan
  * apply_bootstrap_plan — write_scope governance + idempotent
  * GreenfieldBootstrapEditor — env-gated. opt-in 안 됐으면
    ``BootstrapLiveEditorUnavailable`` raise → worker surfaces
    ``bootstrap_required:live_editor_unavailable``
  * scaffold 후 stack signals (package.json) 자연스럽게 detect →
    다음 run 은 JS/TS path 진입

본 모듈은 사용자가 명시한 8 케이스 모두 stdlib unittest 가드.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.coding.greenfield_bootstrap import (
    MODE_GREENFIELD_FULL_STACK,
    MODE_GREENFIELD_PYTHON,
    apply_bootstrap_plan,
    detect_bootstrap_mode,
    plan_greenfield_scaffold,
)
from yule_engineering.agents.job_queue.coding_execute_test_command import (
    STRATEGY_BOOTSTRAP_REQUIRED,
    STRATEGY_JS_SCRIPT,
    select_test_command,
)
from yule_engineering.agents.job_queue.coding_executor_live import (
    BootstrapLiveEditorUnavailable,
    ENV_GREENFIELD_BOOTSTRAP_ENABLED,
    GreenfieldBootstrapEditor,
    SubprocessTestRunner,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    CodingExecutorWorker,
    JOB_TYPE_CODING_EXECUTE,
    REASON_BOOTSTRAP_REQUIRED,
    WorktreeContext,
)
from yule_engineering.agents.job_queue.heartbeat import HeartbeatStore
from yule_engineering.agents.job_queue.state_machine import JobState
from yule_engineering.agents.job_queue.store import JobQueue


_REPO = "yule-studio/naver-search-clone"
_FULL_STACK_PROMPT = (
    "네이버 검색 풀스택 MVP 구현해줘. "
    "Next.js + NestJS + PostgreSQL + Docker Compose."
)


def _request(**overrides) -> CodingExecuteRequest:
    base = dict(
        session_id="11917bf1e75d",
        executor_role="backend-engineer",
        user_request=_FULL_STACK_PROMPT,
        generated_prompt="(prompt)",
        write_scope=(),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name=_REPO,
        issue_number=1,
        dry_run=False,
        metadata={},
    )
    base.update(overrides)
    return CodingExecuteRequest(**base)


class _EnvScope:
    """Context manager: set env vars within block, restore on exit."""

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


def _greenfield_worktree(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "README.md").write_text("# naver-search-clone (greenfield)\n")


# ---------------------------------------------------------------------------
# Cases 1, 2, 5 — detection + scaffold + post-scaffold stack signals
# ---------------------------------------------------------------------------


class DetectAndScaffoldTests(unittest.TestCase):
    def test_empty_full_stack_request_selects_greenfield_full_stack_mode(self) -> None:
        """case 1 — empty repo + full-stack coding request → bootstrap mode."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            mode = detect_bootstrap_mode(
                request=_request(), worktree_path=str(root)
            )
        self.assertEqual(mode, MODE_GREENFIELD_FULL_STACK)

    def test_non_greenfield_repo_no_bootstrap_mode(self) -> None:
        """기존 코드 있는 repo 는 bootstrap mode 아님 (ordinary edit path)."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.ts").write_text("export {};\n")
            (root / "package.json").write_text("{}")
            mode = detect_bootstrap_mode(
                request=_request(), worktree_path=str(root)
            )
        self.assertIsNone(mode)

    def test_greenfield_without_stack_request_no_bootstrap(self) -> None:
        """greenfield 라도 request 가 stack 신호 없으면 (e.g. "그냥 README
        만 업데이트") mode 안 들어감."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            mode = detect_bootstrap_mode(
                request=_request(user_request="README 추가만"),
                worktree_path=str(root),
            )
        self.assertIsNone(mode)

    def test_full_stack_plan_creates_canonical_files(self) -> None:
        plan = plan_greenfield_scaffold(
            mode=MODE_GREENFIELD_FULL_STACK, request=_request()
        )
        paths = [f.relative_path for f in plan.files]
        for required in (
            "package.json",
            "pnpm-workspace.yaml",
            "docker-compose.yml",
            ".env.example",
            ".gitignore",
            "apps/web/package.json",
            "apps/api/package.json",
            "GREENFIELD_BOOTSTRAP.md",
        ):
            self.assertIn(required, paths)

    def test_apply_creates_files_and_is_idempotent(self) -> None:
        """case 5 — scaffold 후 stack signals 가 실제 disk 에 존재. 두
        번째 apply 는 모두 skip."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            r1 = apply_bootstrap_plan(
                worktree_path=str(root), plan=plan, write_scope=()
            )
            self.assertTrue(r1.succeeded)
            self.assertGreaterEqual(len(r1.files_created), 10)
            self.assertTrue((root / "package.json").is_file())
            self.assertTrue((root / "docker-compose.yml").is_file())
            # idempotent
            r2 = apply_bootstrap_plan(
                worktree_path=str(root), plan=plan, write_scope=()
            )
            self.assertEqual(r2.files_created, ())
            self.assertGreaterEqual(len(r2.files_skipped_exists), 10)


# ---------------------------------------------------------------------------
# Case 6 — post-bootstrap stack selection picks JS path
# ---------------------------------------------------------------------------


class PostBootstrapStackSelectionTests(unittest.TestCase):
    def test_post_bootstrap_test_command_uses_js_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            apply_bootstrap_plan(
                worktree_path=str(root), plan=plan, write_scope=()
            )
            # bootstrap done — test command selection 가 JS path 인식
            sel = select_test_command(worktree_path=str(root))
        # scaffold 가 ``package.json`` + ``scripts.test`` 를 만들어 두므로
        # JS_SCRIPT strategy 가 선택됨 (옛 bootstrap_required 가 아님).
        self.assertEqual(sel.strategy, STRATEGY_JS_SCRIPT)
        self.assertNotEqual(sel.strategy, STRATEGY_BOOTSTRAP_REQUIRED)


# ---------------------------------------------------------------------------
# Case 3 — record-only / opt-in disabled surfaces clear reason
# ---------------------------------------------------------------------------


class CapabilityGapSurfaceTests(unittest.TestCase):
    def test_editor_raises_live_editor_unavailable_when_env_off(self) -> None:
        """case 3 — env opt-in 안 됐으면 ``BootstrapLiveEditorUnavailable``
        raise (record-only fallback 으로 silent 진행 안 함)."""

        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: None}):
            editor = GreenfieldBootstrapEditor()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _greenfield_worktree(root)
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                with self.assertRaises(BootstrapLiveEditorUnavailable):
                    editor.apply(request=_request(), context=ctx)

    def test_non_greenfield_editor_delegates_to_record_only(self) -> None:
        """capability gap 은 greenfield 일 때만 — ordinary edit 시나리오
        에서는 editor 가 record-only delegation 정상 진행."""

        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: None}):
            editor = GreenfieldBootstrapEditor()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "package.json").write_text("{}")
                ctx = WorktreeContext(
                    branch="agent/x/y", worktree_path=str(root)
                )
                new_ctx = editor.apply(request=_request(), context=ctx)
                # record-only delegate 가 plan note 작성 — 새 파일 있음
                self.assertGreater(len(new_ctx.edited_files), 0)


# ---------------------------------------------------------------------------
# Case 4 — env on → live scaffold writes files
# ---------------------------------------------------------------------------


class LiveBootstrapEditorTests(unittest.TestCase):
    def test_editor_writes_scaffold_when_env_enabled(self) -> None:
        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            editor = GreenfieldBootstrapEditor()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _greenfield_worktree(root)
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                new_ctx = editor.apply(request=_request(), context=ctx)
                self.assertTrue((root / "package.json").is_file())
                self.assertTrue((root / "docker-compose.yml").is_file())
                self.assertIn("bootstrap_apply", new_ctx.metadata)


# ---------------------------------------------------------------------------
# Case 7 — write_scope governance
# ---------------------------------------------------------------------------


class WriteScopeGovernanceTests(unittest.TestCase):
    def test_write_scope_refuses_files_outside_scope(self) -> None:
        """P1-J: bootstrap-essential exception 을 끄면 (``allow_bootstrap_essentials=False``)
        옛 동작 — ordinary write_scope 만 적용. top-level scaffold 거부."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            # Restrict to ``apps/**`` only — top-level files (package.json,
            # docker-compose.yml, .env.example) should be refused. Exception
            # off so we test pure write_scope semantics.
            result = apply_bootstrap_plan(
                worktree_path=str(root),
                plan=plan,
                write_scope=("apps",),
                allow_bootstrap_essentials=False,
            )
        refused = set(result.files_refused_by_scope)
        self.assertIn("package.json", refused)
        self.assertIn("docker-compose.yml", refused)
        # apps/* files DID get created
        self.assertTrue(
            any(f.startswith("apps/") for f in result.files_created)
        )

    def test_empty_write_scope_allows_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            result = apply_bootstrap_plan(
                worktree_path=str(root), plan=plan, write_scope=()
            )
        self.assertEqual(result.files_refused_by_scope, ())


# ---------------------------------------------------------------------------
# Case 8 — worker mapping for canonical session shape
# ---------------------------------------------------------------------------


class WorkerMappingForCanonicalSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "queue.sqlite"
        self.queue = JobQueue(db_path=self.db_path)
        self.heartbeats = HeartbeatStore(db_path=self.db_path)

    def _seed_job(self, session_id: str, payload: Dict[str, Any]) -> str:
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

    def _run_worker(
        self,
        editor: Any,
        *,
        worktree_root: Path,
    ) -> Any:
        class _Provisioner:
            def provision(self, *, request, branch):
                wt = worktree_root / "wt"
                wt.mkdir(exist_ok=True)
                _greenfield_worktree(wt)
                return WorktreeContext(
                    branch=branch,
                    worktree_path=str(wt),
                    base_commit_sha="abc",
                )

            def cleanup(self, *, force: bool = False) -> None:
                pass

        class _NoOp:
            def commit(self, **k):
                return k["context"]

            def push(self, **k):
                return k["context"]

            def open(self, **k):
                return k["context"]

        payload = {
            "session_id": "11917bf1e75d",
            "executor_role": "backend-engineer",
            "user_request": _FULL_STACK_PROMPT,
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
        job_id = self._seed_job("11917bf1e75d", payload)
        picked = self.queue.pick(
            worker_id="t",
            job_types=(JOB_TYPE_CODING_EXECUTE,),
        )
        assert picked is not None and picked.job_id == job_id
        worker = CodingExecutorWorker(
            queue=self.queue,
            heartbeats=self.heartbeats,
            worktree_provisioner=_Provisioner(),
            code_editor=editor,
            test_runner=SubprocessTestRunner(),
            committer=_NoOp(),
            pusher=_NoOp(),
            draft_pr_creator=_NoOp(),
        )
        return worker.process_job(picked), job_id

    def test_capability_gap_surfaces_live_editor_unavailable_terminal(self) -> None:
        """case 8 — canonical session shape (greenfield + full-stack request)
        + GreenfieldBootstrapEditor with env OFF → terminal fail with
        ``bootstrap_required:live_editor_unavailable:<mode>``."""

        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: None}):
            with tempfile.TemporaryDirectory() as tmp:
                outcome, job_id = self._run_worker(
                    editor=GreenfieldBootstrapEditor(),
                    worktree_root=Path(tmp),
                )
        self.assertIsNotNone(outcome.failure_reason)
        self.assertIn(REASON_BOOTSTRAP_REQUIRED, outcome.failure_reason)
        self.assertIn("live_editor_unavailable", outcome.failure_reason)
        row_after = self.queue.get(job_id)
        self.assertEqual(row_after.state, JobState.FAILED_TERMINAL)


if __name__ == "__main__":
    unittest.main()
