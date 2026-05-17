"""P1-E + P1-F — stack-aware test command + auto repo materialization.

Canonical session ``11917bf1e75d`` 의 latest blocker:
  - target repo (yule-studio/naver-search-clone) 가 Next.js + NestJS +
    PostgreSQL + Docker Compose 인데 worker 가 ``python3 -m unittest``
    default 로 시도 → ``test_failed``.
- 동시에 production 운영을 위해서는 ``target_repo_checkout_missing`` 도
  operator 수동 clone 이 아니라 governed auto-clone 으로 처리되어야 함.

본 모듈은 사용자가 명시한 10 케이스 모두 stdlib unittest 가드:

  P1-E (stack-aware test command)
    1. Python repo without override → python default 유지
    2. JS/TS repo with package.json test script → node test command
    3. Next/Nest style repo → python unittest default 금지
    4. explicit metadata.test_command override 항상 우선
    5. failure surface 에 selected command + selection strategy

  P1-F (auto materialization + governance)
    6. existing local override (resolver injection) 가 항상 우선
    7. no local checkout + auto-clone disabled → 거부 + clear reason
    8. no local checkout + auto-clone enabled + allowed owner + 빈 cache
       → ``cloned`` action 으로 path 반환
    9. existing cache 가 있으면 clone 안 함 → ``fetched`` / ``reused``
   10. disallowed repo owner → ``refused_owner`` (clone 시도 안 함)
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.coding_execute_repo_materializer import (
    ACTION_CLONED,
    ACTION_FAILED,
    ACTION_FETCHED,
    ACTION_REFUSED_DISABLED,
    ACTION_REFUSED_OWNER,
    ACTION_REUSED,
    ENV_ALLOWED_OWNERS,
    ENV_AUTO_CLONE,
    ENV_CACHE_ROOT,
    ENV_CLONE_BASE_URL,
    MaterializationResult,
    materialize_repo,
)
from yule_orchestrator.agents.job_queue.coding_execute_test_command import (
    PYTHON_UNITTEST_DEFAULT,
    STRATEGY_JS_PM_DEFAULT,
    STRATEGY_JS_SCRIPT,
    STRATEGY_METADATA_OVERRIDE,
    STRATEGY_PYTHON_PYTEST,
    STRATEGY_PYTHON_UNITTEST_DEFAULT,
    TestCommandSelection,
    select_test_command,
)
from yule_orchestrator.agents.job_queue.coding_executor_live import (
    LocalGitWorktreeProvisioner,
    SubprocessTestRunner,
    TargetRepoUnavailableError,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


@contextmanager
def _env_override(**values: Optional[str]):
    """Set env vars (or pop when value is None) within block; restore."""

    original: Dict[str, Optional[str]] = {
        key: os.environ.get(key) for key in values
    }
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, original_value in original.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value


def _make_request(metadata: Optional[Mapping[str, Any]] = None) -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="s",
        executor_role="backend-engineer",
        user_request="x",
        generated_prompt="x",
        write_scope=(),
        forbidden_scope=(),
        safety_rules=(),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-1-coding-execute",
        repo_full_name="yule-studio/naver-search-clone",
        issue_number=1,
        dry_run=False,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Cases 1–5 — stack-aware test command
# ---------------------------------------------------------------------------


class StackAwareTestCommandTests(unittest.TestCase):
    def test_python_repo_uses_unittest_default(self) -> None:
        """case 1 — JS/TS / Python signals 없는 repo 는 python unittest
        fallback 유지 (회귀 방지)."""

        with tempfile.TemporaryDirectory() as tmp:
            sel = select_test_command(worktree_path=tmp)
        self.assertEqual(sel.strategy, STRATEGY_PYTHON_UNITTEST_DEFAULT)
        self.assertEqual(sel.command, PYTHON_UNITTEST_DEFAULT)

    def test_python_pytest_repo_uses_pytest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pytest.ini").write_text("[pytest]\n")
            sel = select_test_command(worktree_path=tmp)
        self.assertEqual(sel.strategy, STRATEGY_PYTHON_PYTEST)
        self.assertEqual(sel.command, ("python3", "-m", "pytest"))

    def test_js_repo_with_test_script_uses_pnpm(self) -> None:
        """case 2 — package.json + pnpm-lock + scripts.test → pnpm run test."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest run"}})
            )
            (root / "pnpm-lock.yaml").write_text("")
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_JS_SCRIPT)
        self.assertEqual(sel.command, ("pnpm", "run", "test"))
        self.assertEqual(sel.package_manager, "pnpm")

    def test_full_stack_next_nest_repo_does_not_pick_python(self) -> None:
        """case 3 — canonical session 의 target repo 시뮬: Next.js +
        NestJS + Docker Compose 모양. python unittest default 절대 금지."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "naver-search-clone",
                        "workspaces": ["apps/web", "apps/api"],
                        "scripts": {
                            "test": "turbo run test",
                            "dev": "turbo run dev",
                        },
                    }
                )
            )
            (root / "package-lock.json").write_text("")
            (root / "docker-compose.yml").write_text("services:\n  web: {}\n")
            (root / "apps").mkdir()
            sel = select_test_command(worktree_path=str(root))
        self.assertNotEqual(sel.command, PYTHON_UNITTEST_DEFAULT)
        self.assertNotIn("python3", sel.command)
        self.assertEqual(sel.strategy, STRATEGY_JS_SCRIPT)
        # npm or pm-default — for package-lock.json → npm
        self.assertEqual(sel.package_manager, "npm")

    def test_metadata_override_always_wins(self) -> None:
        """case 4 — operator-provided metadata.test_command 가 항상 우선."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
            sel = select_test_command(
                worktree_path=str(root),
                request_metadata={"test_command": ["make", "test", "--ci"]},
            )
        self.assertEqual(sel.strategy, STRATEGY_METADATA_OVERRIDE)
        self.assertEqual(sel.command, ("make", "test", "--ci"))

    def test_js_repo_without_test_script_falls_back_to_pm_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"name": "x"})  # no scripts
            )
            (root / "yarn.lock").write_text("")
            sel = select_test_command(worktree_path=str(root))
        self.assertEqual(sel.strategy, STRATEGY_JS_PM_DEFAULT)
        self.assertEqual(sel.command, ("yarn", "test"))

    def test_failure_surface_includes_selection(self) -> None:
        """case 5 — SubprocessTestRunner 가 실패 시 test_summary 에
        selection (strategy / command / package_manager) 를 stamp."""

        from yule_orchestrator.agents.job_queue.coding_executor_live import (
            _SubprocessError,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest"}})
            )
            (root / "pnpm-lock.yaml").write_text("")

            calls: List[List[str]] = []

            def _runner(cmd, **_kwargs):
                calls.append(list(cmd))
                raise _SubprocessError(
                    exit_code=1, stdout="", stderr="vitest reported failures"
                )

            runner = SubprocessTestRunner(runner=_runner)
            ctx = WorktreeContext(branch="b", worktree_path=str(root))
            result_ctx = runner.run(
                request=_make_request(), context=ctx
            )
        summary = result_ctx.test_summary
        self.assertEqual(summary.get("status"), "failed")
        self.assertEqual(summary.get("command"), ["pnpm", "run", "test"])
        self.assertEqual(summary["selection"]["strategy"], STRATEGY_JS_SCRIPT)
        self.assertEqual(summary["selection"]["package_manager"], "pnpm")
        self.assertIn("vitest reported failures", summary.get("stderr_tail", ""))


# ---------------------------------------------------------------------------
# Cases 6–10 — auto materialization governance
# ---------------------------------------------------------------------------


class RepoMaterializerTests(unittest.TestCase):
    def test_auto_clone_disabled_by_default(self) -> None:
        """case 7 — env 가 안 켜져 있으면 refused_disabled."""

        with _env_override(**{ENV_AUTO_CLONE: None, ENV_ALLOWED_OWNERS: None}):
            result = materialize_repo(
                repo_full_name="yule-studio/naver-search-clone"
            )
        self.assertEqual(result.action, ACTION_REFUSED_DISABLED)
        self.assertIn(ENV_AUTO_CLONE, result.reason)

    def test_auto_clone_owner_allowlist_refuses_unlisted(self) -> None:
        """case 10 — owner 가 allowlist 에 없으면 refused_owner."""

        with _env_override(
            **{
                ENV_AUTO_CLONE: "1",
                ENV_ALLOWED_OWNERS: "yule-studio",
            }
        ):
            result = materialize_repo(
                repo_full_name="someone-else/private-thing"
            )
        self.assertEqual(result.action, ACTION_REFUSED_OWNER)
        self.assertIn("someone-else", result.reason)

    def test_auto_clone_invokes_git_clone(self) -> None:
        """case 8 — enabled + allowed owner + 빈 cache → cloned."""

        with tempfile.TemporaryDirectory() as cache:
            calls: List[List[str]] = []

            def _runner(cmd, **_kwargs):
                calls.append(list(cmd))
                # synthesize the clone outcome on disk so a real
                # ``git -C`` later wouldn't break — we just create
                # the .git dir so reuse-path detection works for
                # follow-up calls.
                if "clone" in cmd:
                    target = Path(cmd[-1])
                    target.mkdir(parents=True, exist_ok=True)
                    (target / ".git").mkdir(parents=True, exist_ok=True)
                return 0, "", ""

            with _env_override(
                **{
                    ENV_AUTO_CLONE: "1",
                    ENV_ALLOWED_OWNERS: "yule-studio",
                    ENV_CACHE_ROOT: cache,
                    ENV_CLONE_BASE_URL: "https://example.test",
                }
            ):
                result = materialize_repo(
                    repo_full_name="yule-studio/naver-search-clone",
                    runner=_runner,
                )
            self.assertEqual(result.action, ACTION_CLONED)
            self.assertTrue(result.path)
            self.assertIn("naver-search-clone", str(result.path))
            # Clone URL composed from base URL env
            self.assertTrue(
                any(
                    "example.test" in token for cmd in calls for token in cmd
                )
            )

    def test_auto_clone_existing_checkout_fetches(self) -> None:
        """case 9 — cache 에 이미 있으면 git fetch 만 → fetched."""

        with tempfile.TemporaryDirectory() as cache:
            cache_resolved = Path(cache).resolve()
            target = cache_resolved / "yule-studio" / "naver-search-clone"
            (target / ".git").mkdir(parents=True)

            def _runner(cmd, **_kwargs):
                return 0, "", ""

            with _env_override(
                **{
                    ENV_AUTO_CLONE: "1",
                    ENV_ALLOWED_OWNERS: "yule-studio",
                    ENV_CACHE_ROOT: str(cache_resolved),
                }
            ):
                result = materialize_repo(
                    repo_full_name="yule-studio/naver-search-clone",
                    runner=_runner,
                )
            self.assertEqual(result.action, ACTION_FETCHED)
            self.assertEqual(Path(result.path).resolve(), target.resolve())

    def test_auto_clone_existing_checkout_reuses_on_fetch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as cache:
            cache_resolved = Path(cache).resolve()
            target = cache_resolved / "yule-studio" / "naver-search-clone"
            (target / ".git").mkdir(parents=True)

            def _runner(cmd, **_kwargs):
                return 1, "", "fatal: unable to access"

            with _env_override(
                **{
                    ENV_AUTO_CLONE: "1",
                    ENV_ALLOWED_OWNERS: "yule-studio",
                    ENV_CACHE_ROOT: str(cache_resolved),
                }
            ):
                result = materialize_repo(
                    repo_full_name="yule-studio/naver-search-clone",
                    runner=_runner,
                )
            self.assertEqual(result.action, ACTION_REUSED)
            self.assertEqual(Path(result.path).resolve(), target.resolve())

    def test_auto_clone_failure_surfaces_reason(self) -> None:
        """clone 실패 → action=failed + 첫 stderr line 으로 reason."""

        with tempfile.TemporaryDirectory() as cache:
            def _runner(cmd, **_kwargs):
                return 128, "", "fatal: repository 'foo' not found"

            with _env_override(
                **{
                    ENV_AUTO_CLONE: "1",
                    ENV_ALLOWED_OWNERS: "yule-studio",
                    ENV_CACHE_ROOT: cache,
                }
            ):
                result = materialize_repo(
                    repo_full_name="yule-studio/naver-search-clone",
                    runner=_runner,
                )
            self.assertEqual(result.action, ACTION_FAILED)
            self.assertIn("repository 'foo' not found", result.reason)


# ---------------------------------------------------------------------------
# Case 6 — resolver wins; provisioner integrates materializer
# ---------------------------------------------------------------------------


class ProvisionerResolutionChainTests(unittest.TestCase):
    def test_injected_resolver_wins_over_auto_clone(self) -> None:
        """case 6 — explicit ``repo_root_resolver`` injection 은 항상 auto-clone
        보다 우선. 운영자가 local override 를 했다면 그 path 그대로 사용."""

        with tempfile.TemporaryDirectory() as tmp:
            local_path = str(Path(tmp) / "naver-search-clone")
            Path(local_path).mkdir()
            provisioner = LocalGitWorktreeProvisioner(
                repo_root="/tmp/orch",
                worktree_root="/tmp/yule-test-wt",
                repo_root_resolver=lambda _n: local_path,
            )
            request = _make_request()
            resolved = provisioner.resolve_repo_root_for_request(request)
            self.assertEqual(resolved, local_path)

    def test_no_local_checkout_no_auto_clone_raises_with_clear_reason(
        self,
    ) -> None:
        """resolver 도 못 찾고 auto-clone 도 disabled → reason 에 materialization
        outcome 까지 surface."""

        with tempfile.TemporaryDirectory() as orch_tmp:
            provisioner = LocalGitWorktreeProvisioner(
                repo_root=str(Path(orch_tmp) / "orch"),
                worktree_root="/tmp/yule-test-wt",
            )
            Path(provisioner.repo_root).mkdir()
            request = _make_request()
            with _env_override(
                **{ENV_AUTO_CLONE: None, ENV_ALLOWED_OWNERS: None}
            ):
                with self.assertRaises(TargetRepoUnavailableError) as cm:
                    provisioner.resolve_repo_root_for_request(request)
            self.assertIn("materialization", str(cm.exception))
            self.assertIn("refused_disabled", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
