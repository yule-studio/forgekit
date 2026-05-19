"""P1-Z6 — canonical coding path retry + strategy repo-aware + dev watch.

배경
----
canonical session ``6911f9d65e5d`` (job ``1779178961452-f2705c655a9b``)
가 노출한 4 가지 회귀:

1. ``coding_execute`` 가 ``edit_failed: _SubprocessError: subprocess
   failed: exit=124`` 로 죽음 — operator surface 가 generic 해서
   어디서 timeout 났는지 진단 불가.
2. ``failed_retryable`` 인데 실제 자동 재시도 안 됨 — max_attempts=1
   default + recovery sweep 없음.
3. ``strategy_id=single_repo_greenfield_empty`` — 실제 target repo
   (``naver-search-clone``) 는 ``apps/`` 구조였는데 toplevel=[] 로
   잘못 분류.
4. 사용자가 매번 ``runtime up`` 전체 재기동 — dev hot-reload 없음.

본 회귀는 위 4 가지를 영구히 lock.

stdlib unittest 만.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.implementation_strategy import (
    STRATEGY_GREENFIELD_EMPTY,
    STRATEGY_MONOREPO_APPS,
    ROLE_BACKEND,
    scan_toplevel_paths_from_workspace,
    synthesize_implementation_strategy,
)
from yule_orchestrator.agents.job_queue.coding_execute_recovery import (
    TRANSIENT_PRE_PR_RETRYABLE_REASONS,
    recover_transient_pre_pr_failures,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    REASON_EDIT_SUBPROCESS_FAILED,
    REASON_EDIT_TIMEOUT_LIVE_EDITOR,
    REASON_WRITE_SCOPE_RESOLVED_EMPTY,
)
from yule_orchestrator.runtime.dev_watch import (
    AffectedDecision,
    ENGINEERING_SERVICES_SET,
    FileWatcher,
    SERVICE_CODING_EXECUTOR,
    SERVICE_DISCORD_GATEWAY,
    SERVICE_GITHUB_WORK_ORDER_EXECUTOR,
    compute_affected_services,
    dev_watch_enabled,
    run_dev_watch_iteration,
)


# ---------------------------------------------------------------------------
# A — edit timeout structured diagnostics
# ---------------------------------------------------------------------------


class EditTimeoutReasonTests(unittest.TestCase):
    def test_reason_constants_exist_and_distinct(self) -> None:
        self.assertEqual(
            REASON_EDIT_TIMEOUT_LIVE_EDITOR, "edit_timeout_live_editor"
        )
        self.assertEqual(REASON_EDIT_SUBPROCESS_FAILED, "edit_subprocess_failed")
        self.assertNotEqual(
            REASON_EDIT_TIMEOUT_LIVE_EDITOR, REASON_EDIT_SUBPROCESS_FAILED
        )

    def test_worker_source_branches_on_subprocess_error_exit_code(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            coding_executor_worker as worker_mod,
        )

        source = inspect.getsource(worker_mod.CodingExecutorWorker.process_job)
        # exit_code=124 분기 + structured audit
        self.assertIn("_SubprocessError", source)
        self.assertIn("REASON_EDIT_TIMEOUT_LIVE_EDITOR", source)
        self.assertIn("failing_stage", source)
        self.assertIn("subprocess_kind", source)
        self.assertIn("timeout_seconds", source)
        self.assertIn("stderr_tail", source)


# ---------------------------------------------------------------------------
# B — pre-PR retry policy: max_attempts default + recovery sweep
# ---------------------------------------------------------------------------


class PrePRRetryDefaultsTests(unittest.TestCase):
    def test_enqueue_default_max_attempts_is_three(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue.coding_executor_worker import (
            CodingExecutorWorker,
        )

        sig = inspect.signature(CodingExecutorWorker.enqueue)
        self.assertEqual(sig.parameters["max_attempts"].default, 3)

    def test_transient_reasons_set_includes_edit_timeout(self) -> None:
        self.assertIn(
            "edit_timeout_live_editor", TRANSIENT_PRE_PR_RETRYABLE_REASONS
        )
        self.assertIn(
            "edit_subprocess_failed", TRANSIENT_PRE_PR_RETRYABLE_REASONS
        )

    def test_structural_reasons_not_in_transient_set(self) -> None:
        # write_scope_resolved_empty / strategy unresolved 는 retry 대상 아님.
        self.assertNotIn(
            REASON_WRITE_SCOPE_RESOLVED_EMPTY, TRANSIENT_PRE_PR_RETRYABLE_REASONS
        )
        self.assertNotIn(
            "tech_lead_strategy_unresolved", TRANSIENT_PRE_PR_RETRYABLE_REASONS
        )

    def test_recovery_sweep_with_in_memory_queue(self) -> None:
        from yule_orchestrator.agents.job_queue.state_machine import JobState
        from yule_orchestrator.agents.job_queue.store import JobQueue

        tmpdir = tempfile.mkdtemp(prefix="yule-p1z6-retry-")
        queue = JobQueue(db_path=Path(tmpdir) / "queue.sqlite")

        # 1) edit_timeout_live_editor 로 떨어진 row 추가
        job = queue.enqueue(
            session_id="sess-z6-1",
            job_type="coding_execute",
            payload={"session_id": "sess-z6-1", "executor_role": "backend-engineer"},
            max_attempts=3,
        )
        queue.pick(worker_id="w1", job_types=["coding_execute"])
        queue.transition(
            job.job_id,
            JobState.FAILED_RETRYABLE,
            result={"reason": "edit_timeout_live_editor", "branch": "f/x"},
        )

        requeued = recover_transient_pre_pr_failures(queue)
        self.assertIn(job.job_id, requeued)

        # 2) structural reason 은 retry 안 됨
        job2 = queue.enqueue(
            session_id="sess-z6-2",
            job_type="coding_execute",
            payload={"session_id": "sess-z6-2", "executor_role": "backend-engineer"},
            max_attempts=3,
        )
        queue.pick(worker_id="w1", job_types=["coding_execute"])
        queue.transition(
            job2.job_id,
            JobState.FAILED_RETRYABLE,
            result={"reason": REASON_WRITE_SCOPE_RESOLVED_EMPTY, "branch": "f/y"},
        )
        requeued2 = recover_transient_pre_pr_failures(queue)
        self.assertNotIn(job2.job_id, requeued2)


# ---------------------------------------------------------------------------
# C — strategy reads workspace_root local checkout
# ---------------------------------------------------------------------------


class StrategyWorkspaceRootScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "apps" / "web").mkdir(parents=True)
        (self.root / "apps" / "api").mkdir(parents=True)
        (self.root / "packages" / "shared").mkdir(parents=True)
        (self.root / "README.md").touch()

    def test_scan_returns_apps_packages(self) -> None:
        result = scan_toplevel_paths_from_workspace(str(self.root))
        self.assertIn("apps", result)
        self.assertIn("packages", result)

    def test_scan_returns_empty_for_missing_dir(self) -> None:
        result = scan_toplevel_paths_from_workspace("/tmp/definitely-not-p1z6")
        self.assertEqual(result, ())

    def test_scan_returns_empty_for_none(self) -> None:
        self.assertEqual(scan_toplevel_paths_from_workspace(None), ())

    def test_strategy_uses_workspace_root_when_toplevel_empty(self) -> None:
        """canonical session 6911f9d65e5d 의 회귀: toplevel=[] + workspace_root
        주어졌을 때 local scan 으로 apps/ 감지."""

        strategy = synthesize_implementation_strategy(
            user_request="네이버 검색형 풀스택 MVP 구축",
            toplevel_paths=(),
            workspace_root=str(self.root),
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_MONOREPO_APPS)
        self.assertTrue(strategy.resolved)
        self.assertEqual(strategy.first_slice_owner, ROLE_BACKEND)
        self.assertIn("apps/api/**", strategy.first_slice_scope)

    def test_explicit_toplevel_paths_wins_over_workspace(self) -> None:
        """caller 가 명시 toplevel 줬으면 그것이 우선 — workspace scan
        skip."""

        strategy = synthesize_implementation_strategy(
            user_request="검색 구현",
            toplevel_paths=("src", "tests"),
            workspace_root=str(self.root),  # apps/ 있지만
        )
        # toplevel=src/tests 만 → classic_src_layout
        from yule_orchestrator.agents.coding.implementation_strategy import (
            STRATEGY_CLASSIC_SRC_LAYOUT,
        )

        self.assertEqual(strategy.strategy_id, STRATEGY_CLASSIC_SRC_LAYOUT)

    def test_workspace_root_missing_falls_back_to_greenfield(self) -> None:
        strategy = synthesize_implementation_strategy(
            user_request="구현",
            toplevel_paths=(),
            workspace_root=None,
        )
        self.assertEqual(strategy.strategy_id, STRATEGY_GREENFIELD_EMPTY)


# ---------------------------------------------------------------------------
# D — dev watch / selective restart
# ---------------------------------------------------------------------------


class ComputeAffectedServicesTests(unittest.TestCase):
    def test_coding_executor_file_targets_only_coding_executor(self) -> None:
        decision = compute_affected_services(
            ["src/yule_orchestrator/runtime/coding_executor_runner.py"]
        )
        self.assertEqual(decision.services, frozenset({SERVICE_CODING_EXECUTOR}))
        self.assertEqual(decision.reason, "explicit_mapping")

    def test_discord_commands_file_targets_only_discord_gateway(self) -> None:
        decision = compute_affected_services(
            ["src/yule_orchestrator/discord/commands/__init__.py"]
        )
        self.assertEqual(decision.services, frozenset({SERVICE_DISCORD_GATEWAY}))

    def test_github_work_order_file_targets_only_work_order_executor(self) -> None:
        decision = compute_affected_services(
            ["src/yule_orchestrator/agents/job_queue/github_work_order_executor.py"]
        )
        self.assertEqual(
            decision.services, frozenset({SERVICE_GITHUB_WORK_ORDER_EXECUTOR})
        )

    def test_shared_coding_module_targets_both_executor_and_work_order(self) -> None:
        decision = compute_affected_services(
            ["src/yule_orchestrator/agents/coding/implementation_strategy.py"]
        )
        self.assertIn(SERVICE_CODING_EXECUTOR, decision.services)
        self.assertIn(SERVICE_GITHUB_WORK_ORDER_EXECUTOR, decision.services)

    def test_unmapped_orchestrator_file_triggers_conservative_fallback(self) -> None:
        decision = compute_affected_services(
            ["src/yule_orchestrator/agents/something_new/helper.py"]
        )
        self.assertTrue(decision.conservative_fallback)
        self.assertEqual(decision.services, ENGINEERING_SERVICES_SET)
        self.assertEqual(decision.reason, "conservative_fallback")

    def test_unrelated_file_no_restart(self) -> None:
        decision = compute_affected_services(
            ["docs/operations.md", "tests/agents/test_foo.py"]
        )
        self.assertEqual(decision.services, frozenset())
        self.assertEqual(decision.reason, "no_match")

    def test_empty_changes_no_restart(self) -> None:
        decision = compute_affected_services([])
        self.assertEqual(decision.services, frozenset())


class DevWatchOptInTests(unittest.TestCase):
    def test_dev_watch_disabled_by_default(self) -> None:
        import os as _os

        previous = _os.environ.pop("YULE_RUNTIME_DEV_WATCH", None)
        try:
            self.assertFalse(dev_watch_enabled())
        finally:
            if previous is not None:
                _os.environ["YULE_RUNTIME_DEV_WATCH"] = previous

    def test_dev_watch_enabled_by_env(self) -> None:
        import os as _os

        previous = _os.environ.get("YULE_RUNTIME_DEV_WATCH")
        _os.environ["YULE_RUNTIME_DEV_WATCH"] = "1"
        try:
            self.assertTrue(dev_watch_enabled())
        finally:
            if previous is None:
                _os.environ.pop("YULE_RUNTIME_DEV_WATCH", None)
            else:
                _os.environ["YULE_RUNTIME_DEV_WATCH"] = previous


class FileWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "src"
        self.root.mkdir(parents=True)

    def test_first_call_returns_empty_baseline(self) -> None:
        (self.root / "yule_orchestrator").mkdir()
        (self.root / "yule_orchestrator" / "x.py").write_text("a", encoding="utf-8")
        watcher = FileWatcher(roots=[str(self.root)])
        self.assertEqual(watcher.detect_changes(), ())

    def test_modified_file_detected_as_change(self) -> None:
        f = self.root / "y.py"
        f.write_text("v1", encoding="utf-8")
        watcher = FileWatcher(roots=[str(self.root)])
        watcher.detect_changes()  # baseline
        import time as _time

        _time.sleep(0.01)
        f.write_text("v2", encoding="utf-8")
        # touch mtime forward explicitly
        import os as _os

        _os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
        changes = watcher.detect_changes()
        self.assertTrue(any(p.endswith("y.py") for p in changes))


class RunDevWatchIterationTests(unittest.TestCase):
    def test_iteration_no_changes_no_restart(self) -> None:
        class _StaticWatcher:
            interval = 1.0

            def detect_changes(self):
                return ()

        called: list[str] = []
        decision = run_dev_watch_iteration(
            watcher=_StaticWatcher(),  # type: ignore[arg-type]
            restart_service_fn=lambda svc: called.append(svc),
        )
        self.assertEqual(decision.services, frozenset())
        self.assertEqual(called, [])

    def test_iteration_with_change_triggers_restart(self) -> None:
        class _StaticWatcher:
            interval = 1.0
            _calls = 0

            def detect_changes(self):
                self._calls += 1
                if self._calls == 1:
                    return (
                        "src/yule_orchestrator/runtime/coding_executor_runner.py",
                    )
                return ()

        called: list[str] = []
        decision = run_dev_watch_iteration(
            watcher=_StaticWatcher(),  # type: ignore[arg-type]
            restart_service_fn=lambda svc: called.append(svc),
        )
        self.assertEqual(decision.services, frozenset({SERVICE_CODING_EXECUTOR}))
        self.assertEqual(called, [SERVICE_CODING_EXECUTOR])


# ---------------------------------------------------------------------------
# Wiring guards
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_runner_imports_transient_recovery(self) -> None:
        import inspect

        from yule_orchestrator.runtime import coding_executor_runner

        source = inspect.getsource(coding_executor_runner)
        self.assertIn("recover_transient_pre_pr_failures", source)

    def test_slash_intake_passes_workspace_root_to_strategy(self) -> None:
        import inspect

        from yule_orchestrator.discord import commands as cmd_mod

        source = inspect.getsource(cmd_mod._ensure_coding_proposal_on_session)
        self.assertIn("workspace_root", source)
        self.assertIn("_resolve_local_target_repo_root", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
