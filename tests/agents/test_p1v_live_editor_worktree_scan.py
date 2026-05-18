"""P1-V — LiveCodeEditor worktree 변경 수집 + scope 필터 회귀.

P1-U C 가 ``REASON_LIVE_EDITOR_NO_EDITS_PRODUCED`` 로 0-edit 케이스를
정직하게 surface 하긴 하지만, LiveCodeEditor 자체가 claude-cli 가
실제로 worktree 안의 파일을 수정해도 ``edited_files=()`` 로 돌려보내는
한 진짜 코드 commit 은 영영 안 흐른다.  본 모듈은 worktree 스캔 후
파일이 잡혔는지 / write_scope 가 정상 거부했는지 / 0-edit 케이스가
fallback 으로 떨어지는지 확정한다.

stdlib unittest 만 사용 (pytest fixture / pip 의존 X).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_executor_live import (
    ENV_LIVE_EDITOR_ENABLED,
    ENV_LIVE_EDITOR_PROVIDER,
    LiveCodeEditor,
    PROVIDER_CLAUDE_CLI,
    _collect_changed_paths,
    _parse_porcelain_line,
    _path_in_scope,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


def _request(**overrides: Any) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-p1v-scan",
        "executor_role": "backend-engineer",
        "user_request": "implement search clone",
        "generated_prompt": "implement /search route under app/api/search/route.ts",
        "write_scope": ("app/", "components/"),
        "forbidden_scope": (".github/workflows/",),
        "safety_rules": (),
        "base_branch": "main",
        "branch_hint": "feat/search-issue-5",
        "repo_full_name": "yule-studio/naver-search-clone",
        "issue_number": 5,
        "dry_run": False,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


def _worktree_ctx(path: Path) -> WorktreeContext:
    return WorktreeContext(
        branch="feat/search-issue-5",
        worktree_path=str(path),
        base_commit_sha="cafe1234",
    )


class _ProgrammableRunner:
    """fake subprocess runner — 인자 (cmd) 첫 토큰별로 다른 stdout 반환."""

    def __init__(self, *, status_stdout: str = "", raise_on_status: bool = False) -> None:
        self.calls: list[tuple[Sequence[str], Mapping[str, Any]]] = []
        self._status_stdout = status_stdout
        self._raise_on_status = raise_on_status

    def __call__(self, cmd: Sequence[str], **kwargs: Any) -> Mapping[str, Any]:
        cmd_tuple = tuple(cmd)
        self.calls.append((cmd_tuple, dict(kwargs)))
        if "status" in cmd_tuple and "--porcelain" in cmd_tuple:
            if self._raise_on_status:
                raise RuntimeError("git status simulated failure")
            return {"stdout": self._status_stdout, "exit_code": 0}
        return {"stdout": "ok", "exit_code": 0}


# ---------------------------------------------------------------------------
# Porcelain parser
# ---------------------------------------------------------------------------


class PorcelainParserTests(unittest.TestCase):
    def test_modified_line(self) -> None:
        self.assertEqual(_parse_porcelain_line(" M app/api/search/route.ts"), "app/api/search/route.ts")

    def test_added_staged_line(self) -> None:
        self.assertEqual(_parse_porcelain_line("A  components/SearchBox.tsx"), "components/SearchBox.tsx")

    def test_untracked_line(self) -> None:
        self.assertEqual(_parse_porcelain_line("?? app/page.tsx"), "app/page.tsx")

    def test_rename_takes_destination(self) -> None:
        rel = _parse_porcelain_line("R  old/path.ts -> new/path.ts")
        self.assertEqual(rel, "new/path.ts")

    def test_fully_deleted_is_dropped(self) -> None:
        # committer 가 stage 못 함.  drop 하면 안전.
        self.assertIsNone(_parse_porcelain_line(" D removed.ts"))

    def test_ignored_class_dropped(self) -> None:
        self.assertIsNone(_parse_porcelain_line("!! .DS_Store"))

    def test_too_short_returns_none(self) -> None:
        self.assertIsNone(_parse_porcelain_line(""))
        self.assertIsNone(_parse_porcelain_line("ok"))

    def test_quoted_path_unwrapped(self) -> None:
        rel = _parse_porcelain_line('?? "components/Search Box.tsx"')
        self.assertEqual(rel, "components/Search Box.tsx")


# ---------------------------------------------------------------------------
# Path-in-scope matcher
# ---------------------------------------------------------------------------


class PathInScopeTests(unittest.TestCase):
    def test_empty_scope_means_no_restriction(self) -> None:
        self.assertTrue(_path_in_scope("any/file.ts", ()))

    def test_direct_prefix_match(self) -> None:
        self.assertTrue(_path_in_scope("app/page.tsx", ("app/",)))

    def test_glob_suffix_normalized(self) -> None:
        # ``services/auth/**`` / ``services/auth/`` 같은 모양도 같은 prefix.
        self.assertTrue(_path_in_scope("services/auth/login.py", ("services/auth/**",)))
        self.assertTrue(_path_in_scope("services/auth/login.py", ("services/auth",)))

    def test_no_match_returns_false(self) -> None:
        self.assertFalse(_path_in_scope("infrastructure/k8s.yaml", ("app/",)))


# ---------------------------------------------------------------------------
# _collect_changed_paths — scope 필터링 회귀
# ---------------------------------------------------------------------------


class CollectChangedPathsTests(unittest.TestCase):
    def test_in_scope_files_collected(self) -> None:
        runner = _ProgrammableRunner(
            status_stdout=" M app/api/search/route.ts\n?? components/SearchBox.tsx\n",
        )
        detected, refused_scope, refused_forbidden = _collect_changed_paths(
            runner=runner,
            worktree_path="/tmp/fake",
            write_scope=("app/", "components/"),
            forbidden_scope=(),
        )
        self.assertEqual(detected, ("app/api/search/route.ts", "components/SearchBox.tsx"))
        self.assertEqual(refused_scope, ())
        self.assertEqual(refused_forbidden, ())

    def test_out_of_scope_files_refused(self) -> None:
        runner = _ProgrammableRunner(
            status_stdout=" M app/page.tsx\n M infrastructure/k8s.yaml\n",
        )
        detected, refused_scope, _ = _collect_changed_paths(
            runner=runner,
            worktree_path="/tmp/fake",
            write_scope=("app/",),
            forbidden_scope=(),
        )
        self.assertEqual(detected, ("app/page.tsx",))
        self.assertEqual(refused_scope, ("infrastructure/k8s.yaml",))

    def test_forbidden_scope_rejects_even_in_write_scope(self) -> None:
        # ``.github/workflows/`` 는 write_scope 가 broad 해도 forbidden 으로 reject.
        runner = _ProgrammableRunner(
            status_stdout=" M .github/workflows/ci.yaml\n M app/page.tsx\n",
        )
        detected, refused_scope, refused_forbidden = _collect_changed_paths(
            runner=runner,
            worktree_path="/tmp/fake",
            write_scope=(".github/", "app/"),
            forbidden_scope=(".github/workflows/",),
        )
        self.assertEqual(detected, ("app/page.tsx",))
        self.assertEqual(refused_scope, ())
        self.assertEqual(refused_forbidden, (".github/workflows/ci.yaml",))

    def test_runner_raises_returns_empty(self) -> None:
        runner = _ProgrammableRunner(raise_on_status=True)
        detected, refused_scope, refused_forbidden = _collect_changed_paths(
            runner=runner,
            worktree_path="/tmp/fake",
            write_scope=("app/",),
            forbidden_scope=(),
        )
        self.assertEqual((detected, refused_scope, refused_forbidden), ((), (), ()))

    def test_empty_worktree_path_returns_empty(self) -> None:
        runner = _ProgrammableRunner(status_stdout=" M app/page.tsx\n")
        detected, refused_scope, refused_forbidden = _collect_changed_paths(
            runner=runner,
            worktree_path="",
            write_scope=("app/",),
            forbidden_scope=(),
        )
        self.assertEqual((detected, refused_scope, refused_forbidden), ((), (), ()))
        # runner 도 호출 안 함
        self.assertEqual(runner.calls, [])

    def test_dedupes_repeated_paths(self) -> None:
        # rare but possible if git status emits both staged and unstaged
        # entries for the same path.
        runner = _ProgrammableRunner(
            status_stdout="MM app/page.tsx\n M app/page.tsx\n",
        )
        detected, _, _ = _collect_changed_paths(
            runner=runner,
            worktree_path="/tmp/fake",
            write_scope=("app/",),
            forbidden_scope=(),
        )
        self.assertEqual(detected, ("app/page.tsx",))


# ---------------------------------------------------------------------------
# LiveCodeEditor.apply — 진짜 변경 surface
# ---------------------------------------------------------------------------


class LiveCodeEditorAppliesChangesTests(unittest.TestCase):
    def test_changed_files_populated_after_runner(self) -> None:
        runner = _ProgrammableRunner(
            status_stdout=" M app/api/search/route.ts\n?? components/SearchBox.tsx\n",
        )
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=runner,
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _worktree_ctx(Path(tmp))
            new_ctx = editor.apply(request=_request(), context=ctx)
        self.assertEqual(
            new_ctx.edited_files,
            ("app/api/search/route.ts", "components/SearchBox.tsx"),
        )
        # metadata 에 live audit 영속.
        audit = dict(new_ctx.metadata or {}).get("live_editor_apply")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["provider"], PROVIDER_CLAUDE_CLI)
        self.assertEqual(
            audit["detected_changed_files"],
            ["app/api/search/route.ts", "components/SearchBox.tsx"],
        )
        self.assertEqual(audit["refused_by_scope"], [])
        self.assertEqual(audit["refused_by_forbidden"], [])

    def test_zero_edits_returns_context_identity(self) -> None:
        # 진짜 0건 → context 그대로 → worker 의 P1-U C 가 surface
        runner = _ProgrammableRunner(status_stdout="")
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=runner,
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _worktree_ctx(Path(tmp))
            new_ctx = editor.apply(request=_request(), context=ctx)
        self.assertIs(new_ctx, ctx)
        self.assertEqual(new_ctx.edited_files, ())

    def test_refused_files_surface_in_metadata_but_not_committed(self) -> None:
        runner = _ProgrammableRunner(
            status_stdout=" M app/page.tsx\n M infrastructure/k8s.yaml\n",
        )
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=runner,
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _worktree_ctx(Path(tmp))
            new_ctx = editor.apply(
                request=_request(write_scope=("app/",)),
                context=ctx,
            )
        # commit 단계에 흘러가는 건 in-scope 파일만.
        self.assertEqual(new_ctx.edited_files, ("app/page.tsx",))
        audit = dict(new_ctx.metadata or {})["live_editor_apply"]
        self.assertEqual(audit["refused_by_scope"], ["infrastructure/k8s.yaml"])

    def test_git_status_runner_call_uses_capture_output(self) -> None:
        # 운영진단용 — runner 가 stdout 을 받아야 worktree 변경 수집이 됨.
        runner = _ProgrammableRunner(status_stdout="?? app/x.ts\n")
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=runner,
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _worktree_ctx(Path(tmp))
            editor.apply(request=_request(), context=ctx)
        status_calls = [
            (cmd, kwargs)
            for cmd, kwargs in runner.calls
            if "status" in cmd and "--porcelain" in cmd
        ]
        self.assertEqual(len(status_calls), 1)
        status_cmd, status_kwargs = status_calls[0]
        self.assertEqual(status_cmd[:2], ("git", "-C"))
        self.assertTrue(status_kwargs.get("capture_output"))


# ---------------------------------------------------------------------------
# Source-grep guard — wiring 회귀
# ---------------------------------------------------------------------------


class WorktreeScanWiringGuardTests(unittest.TestCase):
    def test_live_editor_calls_collect_changed_paths(self) -> None:
        # 누군가 refactor 로 _collect_changed_paths 호출을 빼버리면 회귀.
        import inspect

        from yule_orchestrator.agents.job_queue import coding_executor_live as mod

        source = inspect.getsource(mod.LiveCodeEditor._apply_via_claude_cli)
        self.assertIn("_collect_changed_paths", source)
        self.assertIn("write_scope", source)
        self.assertIn("forbidden_scope", source)

    def test_module_exports_collect_helper(self) -> None:
        from yule_orchestrator.agents.job_queue import coding_executor_live as mod

        self.assertTrue(callable(getattr(mod, "_collect_changed_paths", None)))
        self.assertTrue(callable(getattr(mod, "_parse_porcelain_line", None)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
