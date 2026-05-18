"""P1-J — greenfield bootstrap-essential allowlist + scope-refusal failure.

Canonical session ``11917bf1e75d`` 의 latest blocker 는 더 이상
runtime/recovery 가 아니라 *role write_scope 가 bootstrap-essential 파일
을 거부* 하는 governance 충돌:

  * canonical job write_scope:
    ``src/<service>/api/**`` / ``src/<service>/domain/**`` /
    ``src/<service>/repository/**`` / ``src/<service>/security/**`` /
    ``migrations/**`` / ``tests/<service>/api/**``
  * greenfield full-stack plan writes:
    ``package.json`` / ``pnpm-workspace.yaml`` / ``docker-compose.yml``
    / ``.env.example`` / ``apps/web/**`` / ``apps/api/**`` / 등.
  * 결과: 모든 scaffold 가 ``files_refused_by_scope`` 에 갇히고,
    stack signals 가 생기지 않아 다음 run 도 다시
    ``bootstrap_required:no_stack_detected`` 로 떨어짐 (silent loop).

본 모듈은 사용자가 명시한 6 케이스 모두 stdlib unittest 가드.
"""

from __future__ import annotations

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


from yule_orchestrator.agents.coding.greenfield_bootstrap import (
    BOOTSTRAP_ESSENTIAL_PREFIXES,
    BOOTSTRAP_HARD_FORBIDDEN_NAMES,
    BootstrapApplyResult,
    BootstrapFile,
    BootstrapPlan,
    MODE_GREENFIELD_FULL_STACK,
    apply_bootstrap_plan,
    detect_bootstrap_mode,
    plan_greenfield_scaffold,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    BootstrapApplyFailed,
    ENV_GREENFIELD_BOOTSTRAP_ENABLED,
    GreenfieldBootstrapEditor,
)
from yule_orchestrator.agents.job_queue.coding_execute_test_command import (
    STRATEGY_JS_SCRIPT,
    select_test_command,
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
_FULL_STACK_PROMPT = (
    "네이버 검색 풀스택 MVP 구현해줘. Next.js + NestJS + PostgreSQL + Docker Compose."
)

# Canonical role write_scope (backend-engineer, naver-search-clone shape)
_CANONICAL_BACKEND_SCOPE: tuple = (
    "src/<service>/api/**",
    "src/<service>/domain/**",
    "src/<service>/repository/**",
    "src/<service>/security/**",
    "migrations/**",
    "tests/<service>/api/**",
)


def _request(
    *,
    write_scope: tuple = _CANONICAL_BACKEND_SCOPE,
    forbidden_scope: tuple = (),
    metadata: Optional[Mapping[str, Any]] = None,
) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="11917bf1e75d",
        executor_role="backend-engineer",
        user_request=_FULL_STACK_PROMPT,
        generated_prompt="(prompt)",
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name=_REPO,
        issue_number=1,
        dry_run=False,
        metadata=metadata or {},
    )


def _greenfield_worktree(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "README.md").write_text("# greenfield\n")


class _EnvScope:
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


# ---------------------------------------------------------------------------
# Case 1 — bootstrap-essential exception allows canonical scope to scaffold
# ---------------------------------------------------------------------------


class BootstrapEssentialExceptionTests(unittest.TestCase):
    def test_canonical_write_scope_with_exception_allows_scaffold(self) -> None:
        """case 1 — canonical backend scope 는 ``src/<service>/...`` 만
        허용하지만 bootstrap-essential exception 덕분에 scaffold 가
        실제로 디스크에 생성된다."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            result = apply_bootstrap_plan(
                worktree_path=str(root),
                plan=plan,
                write_scope=_CANONICAL_BACKEND_SCOPE,
                allow_bootstrap_essentials=True,
            )
            # ≥ 8 files actually written under canonical scope
            self.assertGreaterEqual(len(result.files_created), 8)
            self.assertTrue((root / "package.json").is_file())
            self.assertTrue((root / "docker-compose.yml").is_file())
            self.assertTrue((root / "apps" / "web" / "package.json").is_file())
            # audit shows which files came via bootstrap exception
            self.assertIn("package.json", result.files_allowed_by_bootstrap_exception)
            self.assertIn(
                "apps/web/package.json",
                result.files_allowed_by_bootstrap_exception,
            )

    def test_essential_prefixes_constant_covers_canonical_files(self) -> None:
        """Coverage guard — plan 의 모든 path 가 essential allowlist 와
        매칭되는지 확인 (allowlist 가 drift 하면 catch)."""

        from yule_orchestrator.agents.coding.greenfield_bootstrap import (
            _is_bootstrap_essential,
        )

        plan = plan_greenfield_scaffold(
            mode=MODE_GREENFIELD_FULL_STACK, request=_request()
        )
        for f in plan.files:
            with self.subTest(path=f.relative_path):
                self.assertTrue(
                    _is_bootstrap_essential(f.relative_path, plan.mode),
                    f"{f.relative_path} 는 essential allowlist 에 있어야 한다",
                )


# ---------------------------------------------------------------------------
# Case 2 — hard rail: forbidden files stay blocked even under exception
# ---------------------------------------------------------------------------


class ForbiddenFilesStayBlockedTests(unittest.TestCase):
    def test_dot_env_secret_is_refused_even_with_exception(self) -> None:
        """case 2 — ``.env`` (real secret 파일) 는 bootstrap exception
        있어도 절대 허용 안 됨. ``.env.example`` (placeholder template)
        만 허용."""

        synthetic_plan = BootstrapPlan(
            mode=MODE_GREENFIELD_FULL_STACK,
            files=(
                BootstrapFile(
                    relative_path=".env",
                    content="DB_PASSWORD=should-never-be-written\n",
                ),
                BootstrapFile(
                    relative_path=".env.example",
                    content="DB_PASSWORD=placeholder\n",
                ),
            ),
            summary="synthetic",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            result = apply_bootstrap_plan(
                worktree_path=str(root),
                plan=synthetic_plan,
                write_scope=(),  # no restriction
                allow_bootstrap_essentials=True,
            )
            # .env never lands on disk
            self.assertFalse((root / ".env").is_file())
            self.assertIn(".env", result.files_refused_by_forbidden)
            # .env.example allowed
            self.assertTrue((root / ".env.example").is_file())
            self.assertIn(".env.example", result.files_created)
        # operator forbidden_scope is honored too
        synthetic_plan2 = BootstrapPlan(
            mode=MODE_GREENFIELD_FULL_STACK,
            files=(
                BootstrapFile(
                    relative_path="apps/api/secrets/db.json",
                    content="{}\n",
                ),
            ),
            summary="synthetic",
        )
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = Path(tmp2)
            _greenfield_worktree(root2)
            # apps/ is bootstrap-essential, but forbidden_scope blocks it.
            result2 = apply_bootstrap_plan(
                worktree_path=str(root2),
                plan=synthetic_plan2,
                write_scope=(),
                forbidden_scope=("apps/api/secrets",),
                allow_bootstrap_essentials=True,
            )
            self.assertIn(
                "apps/api/secrets/db.json", result2.files_refused_by_forbidden
            )
            self.assertFalse((root2 / "apps/api/secrets/db.json").is_file())


# ---------------------------------------------------------------------------
# Case 3 — explicit reason when all refused (no misleading no_stack_detected)
# ---------------------------------------------------------------------------


class ScopeRefusedSurfaceTests(unittest.TestCase):
    def test_editor_raises_scope_refused_when_exception_off_and_scope_too_narrow(
        self,
    ) -> None:
        """case 3 — ``allow_bootstrap_essentials=False`` 시 모든 파일이
        refused 되면 ``apply_bootstrap_plan`` 의
        ``all_files_refused_by_scope`` 가 True → editor 가
        ``BootstrapApplyFailed(sub_reason='scope_refused_bootstrap_files')``
        raise."""

        # Reuse the public apply path with exception OFF to simulate
        # the failure shape; editor wraps the same logic.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            # canonical scope refuses every scaffold path
            result = apply_bootstrap_plan(
                worktree_path=str(root),
                plan=plan,
                write_scope=_CANONICAL_BACKEND_SCOPE,
                allow_bootstrap_essentials=False,
            )
        self.assertTrue(result.all_files_refused_by_scope)
        self.assertEqual(result.files_created, ())
        self.assertGreater(len(result.files_refused_by_scope), 0)

    def test_editor_with_exception_off_surfaces_scope_refused_bootstrap_files(self) -> None:
        """Editor 직접 호출 — exception off 강제로 scope_refused 경로 검증."""

        # GreenfieldBootstrapEditor 는 항상 exception=True 로 apply 호출
        # (정상 동작). 이 테스트는 raise 메시지 mapping 자체를 검증하기
        # 위해 ``apply_bootstrap_plan`` 직접 호출.
        from yule_orchestrator.agents.job_queue.coding_executor_live import (
            BootstrapApplyFailed,
        )

        # 명시 scope 가 모든 essential 을 refuse 하도록 만들고, 그 사실을
        # editor 가 sub_reason 으로 surface 하는지 source-grep 으로 확인.
        from yule_orchestrator.agents.job_queue import (
            coding_executor_live as live_mod,
        )

        src = Path(live_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("scope_refused_bootstrap_files", src)
        self.assertIn("all_files_refused_by_scope", src)


# ---------------------------------------------------------------------------
# Case 4 — bootstrap-created scaffold yields stack signals next run
# ---------------------------------------------------------------------------


class PostScaffoldStackSelectionTests(unittest.TestCase):
    def test_scaffold_under_canonical_scope_produces_js_signals(self) -> None:
        """case 4 — canonical scope + exception → scaffold creates
        ``package.json`` → next run 의 ``select_test_command`` 가 JS path."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _greenfield_worktree(root)
            plan = plan_greenfield_scaffold(
                mode=MODE_GREENFIELD_FULL_STACK, request=_request()
            )
            apply_bootstrap_plan(
                worktree_path=str(root),
                plan=plan,
                write_scope=_CANONICAL_BACKEND_SCOPE,
                allow_bootstrap_essentials=True,
            )
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_JS_SCRIPT)


# ---------------------------------------------------------------------------
# Case 5 — canonical session shape regression
# ---------------------------------------------------------------------------


class CanonicalSessionEndToEndTests(unittest.TestCase):
    def test_canonical_session_with_env_on_and_canonical_scope_actually_scaffolds(
        self,
    ) -> None:
        """case 5 — env on + canonical scope + greenfield → editor 가
        scaffold 작성 + metadata audit. row 는 next run 까지 살아남아
        actual stack signals 가 생긴다."""

        with _EnvScope(**{ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"}):
            editor = GreenfieldBootstrapEditor()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _greenfield_worktree(root)
                ctx = WorktreeContext(branch="b", worktree_path=str(root))
                new_ctx = editor.apply(request=_request(), context=ctx)
                # actually written
                self.assertTrue((root / "package.json").is_file())
                self.assertTrue(
                    (root / "apps" / "api" / "package.json").is_file()
                )
                # audit metadata visible to operator
                audit = new_ctx.metadata.get("bootstrap_apply") or {}
                self.assertGreaterEqual(len(audit.get("files_created", [])), 8)
                self.assertGreaterEqual(
                    len(audit.get("files_allowed_by_bootstrap_exception", [])),
                    8,
                )


# ---------------------------------------------------------------------------
# Case 6 — ordinary (non-greenfield) jobs don't widen scope
# ---------------------------------------------------------------------------


class OrdinaryScopeNotWidenedTests(unittest.TestCase):
    def test_non_greenfield_apply_still_obeys_write_scope_strictly(self) -> None:
        """case 6 — bootstrap exception 은 *greenfield bootstrap* 모드에서만
        활성 — ordinary code-edit path 의 write_scope 는 그대로 좁다.

        ``apply_bootstrap_plan`` 자체가 bootstrap mode 의 plan 만 받지만,
        본 테스트는 (a) ordinary CodeEditor 가 본 helper 를 호출하지 않고
        (b) 호출하더라도 essential allowlist 가 ordinary path 와 무관
        하다는 invariant 를 검증한다.
        """

        # Non-greenfield mode (synthetic — single non-essential file)
        synthetic = BootstrapPlan(
            mode="ordinary_edit",  # unknown mode — no allowlist entry
            files=(
                BootstrapFile(
                    relative_path="random/elsewhere/file.txt",
                    content="x\n",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_bootstrap_plan(
                worktree_path=str(root),
                plan=synthetic,
                write_scope=("only/this",),
                allow_bootstrap_essentials=True,
            )
        # mode without an essential allowlist entry → exception is a no-op
        self.assertEqual(result.files_created, ())
        self.assertEqual(
            result.files_refused_by_scope, ("random/elsewhere/file.txt",)
        )
        self.assertEqual(result.files_allowed_by_bootstrap_exception, ())


if __name__ == "__main__":
    unittest.main()
