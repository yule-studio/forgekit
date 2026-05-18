"""P1-T — build_live_executor 가 LiveCodeEditor 우선 선택.

옛 회귀: build_live_executor 가 무조건 GreenfieldBootstrapEditor() 만
사용 → 운영자가 YULE_LIVE_EDITOR_ENABLED=true 를 set 해도 production
runtime 이 LiveCodeEditor 를 절대 선택 안 함 → non-greenfield repo 가
record-only delegate 로 떨어짐 → planning markdown 만 commit.

사용자 명시 7 acceptance:

1. non-greenfield repo no longer silently falls back to record-only
2. issue #5-like context resolves into real edit capable path when env on
3. missing required env yields explicit blocker (not silent plan-only)
4. build_live_executor / build_live_editor_from_env wiring actually selects
   live editor
5. planning markdown-only output not the primary success path for
   non-greenfield implementation requests
6. issue #5 continuity preserved (env / wiring changes only — no issue
   side effects)
7. existing greenfield bootstrap behavior does not regress
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.coding_executor_live import (
    ENV_GREENFIELD_BOOTSTRAP_ENABLED,
    ENV_LIVE_EDITOR_ENABLED,
    ENV_LIVE_EDITOR_PROVIDER,
    GreenfieldBootstrapEditor,
    LiveCodeEditor,
    PROVIDER_CLAUDE_CLI,
    build_live_executor,
    build_live_editor_from_env,
    detect_live_executor_availability,
)


# ---------------------------------------------------------------------------
# 1, 4 — env on → LiveCodeEditor 가 실제 선택
# ---------------------------------------------------------------------------


class BuildLiveExecutorPicksLiveEditorTests(unittest.TestCase):
    def test_env_on_with_claude_cli_picks_live_code_editor(self) -> None:
        bundle = build_live_executor(
            repo_root="/tmp/repo",
            env={
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
            },
        )
        self.assertIsInstance(bundle["code_editor"], LiveCodeEditor)
        self.assertEqual(bundle["code_editor"].provider, PROVIDER_CLAUDE_CLI)

    def test_env_off_falls_back_to_greenfield_bootstrap(self) -> None:
        bundle = build_live_executor(repo_root="/tmp/repo", env={})
        self.assertIsInstance(
            bundle["code_editor"], GreenfieldBootstrapEditor
        )

    def test_env_on_but_no_provider_falls_back(self) -> None:
        # ENABLED=true 인데 PROVIDER 가 비어 있으면 build_live_editor_from_env
        # 가 None 반환 → GreenfieldBootstrapEditor 로 fallback
        bundle = build_live_executor(
            repo_root="/tmp/repo",
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        self.assertIsInstance(
            bundle["code_editor"], GreenfieldBootstrapEditor
        )

    def test_env_on_with_unrelated_provider_still_returns_live_editor(self) -> None:
        # provider=anthropic 도 build_live_editor_from_env 는 LiveCodeEditor
        # 반환 (apply 시점에 BlockedLiveEditorError raise) — 옛 wiring 회귀
        # 가드: 어떤 provider 든 build_live_executor 가 LiveCodeEditor 를
        # 받으면 그것을 사용.
        bundle = build_live_executor(
            repo_root="/tmp/repo",
            env={
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: "anthropic",
            },
        )
        self.assertIsInstance(bundle["code_editor"], LiveCodeEditor)


# ---------------------------------------------------------------------------
# 2 — issue #5 context (non-greenfield repo + live editor on) wires real edit
# ---------------------------------------------------------------------------


class Issue5ContextRealEditWiringTests(unittest.TestCase):
    def test_non_greenfield_repo_with_live_editor_on_uses_live_code_editor(
        self,
    ) -> None:
        """naver-search-clone (non-greenfield) + YULE_LIVE_EDITOR_ENABLED=true
        → bundle.code_editor 는 LiveCodeEditor.  옛 GreenfieldBootstrap 의
        record-only delegate 분기로 떨어지지 않음."""

        bundle = build_live_executor(
            repo_root="/tmp/naver-search-clone",
            env={
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
            },
        )
        editor = bundle["code_editor"]
        self.assertIsInstance(editor, LiveCodeEditor)
        # provider snapshot — operator audit
        self.assertEqual(editor.provider, PROVIDER_CLAUDE_CLI)


# ---------------------------------------------------------------------------
# 3 — missing env → operator-visible blocker reason
# ---------------------------------------------------------------------------


class AvailabilitySurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                ENV_LIVE_EDITOR_ENABLED,
                ENV_LIVE_EDITOR_PROVIDER,
                ENV_GREENFIELD_BOOTSTRAP_ENABLED,
            )
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_off_surface_says_set_live_editor_or_greenfield(self) -> None:
        for k in (
            ENV_LIVE_EDITOR_ENABLED,
            ENV_LIVE_EDITOR_PROVIDER,
            ENV_GREENFIELD_BOOTSTRAP_ENABLED,
        ):
            os.environ.pop(k, None)
        av = detect_live_executor_availability(repo_root="/tmp/r")
        self.assertIn("disabled", av.code_editor)
        self.assertIn(ENV_LIVE_EDITOR_ENABLED, av.code_editor_blocker)

    def test_env_on_surface_says_live_llm_provider(self) -> None:
        os.environ[ENV_LIVE_EDITOR_ENABLED] = "true"
        os.environ[ENV_LIVE_EDITOR_PROVIDER] = PROVIDER_CLAUDE_CLI
        av = detect_live_executor_availability(repo_root="/tmp/r")
        self.assertEqual(av.code_editor, f"live_llm({PROVIDER_CLAUDE_CLI})")
        self.assertEqual(av.code_editor_blocker, "")

    def test_only_greenfield_on_surface_says_record_only_with_hint(self) -> None:
        os.environ.pop(ENV_LIVE_EDITOR_ENABLED, None)
        os.environ.pop(ENV_LIVE_EDITOR_PROVIDER, None)
        os.environ[ENV_GREENFIELD_BOOTSTRAP_ENABLED] = "1"
        av = detect_live_executor_availability(repo_root="/tmp/r")
        self.assertIn("record_only_delegate", av.code_editor)
        self.assertIn(ENV_LIVE_EDITOR_ENABLED, av.code_editor_blocker)


# ---------------------------------------------------------------------------
# 5 — planning-only PR 회귀 가드 (governance.PolicyViolation 흐름 활용)
# ---------------------------------------------------------------------------


class PlanningOnlyPRForbidsRegressionTests(unittest.TestCase):
    """YULE_CODING_EXECUTOR_PLANNING_ONLY_PR_FORBIDDEN=1 + live editor off
    + non-greenfield → editor.apply 가 NonGreenfieldRealEditUnavailable
    raise.  operator 가 명시 opt-out 했을 때 silent plan-only commit
    회귀 차단 (P1-M F 이미 한 동작 + 본 round 의 새 LiveCodeEditor 분기
    가 그 위에서 우선)."""

    def test_planning_forbidden_env_blocks_non_greenfield_delegate(self) -> None:
        from yule_orchestrator.agents.job_queue.coding_executor_live import (
            ENV_PLANNING_ONLY_PR_FORBIDDEN,
            NonGreenfieldRealEditUnavailable,
        )
        from yule_orchestrator.agents.job_queue.coding_executor_worker import (
            CodingExecuteRequest,
            WorktreeContext,
        )

        editor = GreenfieldBootstrapEditor(
            env={ENV_PLANNING_ONLY_PR_FORBIDDEN: "1"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            (root / ".git").mkdir()
            ctx = WorktreeContext(branch="b", worktree_path=str(root))
            req = CodingExecuteRequest(
                session_id="s",
                executor_role="backend-engineer",
                user_request="r",
                generated_prompt="p",
                write_scope=("src/**",),
                forbidden_scope=(),
                safety_rules=(),
                base_branch="main",
                branch_hint="agent/x",
                repo_full_name="yule-studio/naver-search-clone",
                issue_number=5,
                dry_run=False,
                metadata={},
            )
            with self.assertRaises(NonGreenfieldRealEditUnavailable):
                editor.apply(request=req, context=ctx)


# ---------------------------------------------------------------------------
# 7 — existing greenfield bootstrap behavior 회귀 없음
# ---------------------------------------------------------------------------


class GreenfieldBootstrapRegressionTests(unittest.TestCase):
    def test_env_off_default_bundle_still_uses_greenfield_editor(self) -> None:
        """본 round 수정으로 옛 default 동작이 깨지면 안 됨 — live editor
        env 미설정 + greenfield env 미설정 → 옛 GreenfieldBootstrapEditor."""

        bundle = build_live_executor(repo_root="/tmp/r", env={})
        self.assertIsInstance(
            bundle["code_editor"], GreenfieldBootstrapEditor
        )

    def test_greenfield_env_on_alone_picks_greenfield_editor(self) -> None:
        bundle = build_live_executor(
            repo_root="/tmp/r",
            env={ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1"},
        )
        # live editor env 없으면 옛대로 GreenfieldBootstrapEditor 선택
        # (그 안에서 bootstrap_capable=True 인 상태)
        self.assertIsInstance(
            bundle["code_editor"], GreenfieldBootstrapEditor
        )

    def test_live_editor_overrides_greenfield_when_both_on(self) -> None:
        """둘 다 켜져 있으면 LiveCodeEditor 우선 — 사용자 의도 (LLM 편집
        가능 환경) 에 맞음."""

        bundle = build_live_executor(
            repo_root="/tmp/r",
            env={
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
                ENV_GREENFIELD_BOOTSTRAP_ENABLED: "1",
            },
        )
        self.assertIsInstance(bundle["code_editor"], LiveCodeEditor)


# ---------------------------------------------------------------------------
# Bonus — _default_claude_cli_subprocess_runner 가 LiveCodeEditor 에 주입됨
# ---------------------------------------------------------------------------


class DefaultSubprocessRunnerWiredTests(unittest.TestCase):
    def test_live_editor_has_runner_injected(self) -> None:
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
            },
            subprocess_runner=lambda cmd, **kw: None,
        )
        self.assertIsNotNone(editor)
        self.assertIsNotNone(editor._subprocess_runner)

    def test_build_live_executor_injects_default_runner(self) -> None:
        bundle = build_live_executor(
            repo_root="/tmp/r",
            env={
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
            },
        )
        editor = bundle["code_editor"]
        self.assertIsInstance(editor, LiveCodeEditor)
        # runner 가 None 이면 안 됨 — 옛 회귀 (LiveCodeEditor 가 매번
        # "runner not injected" 로 BlockedLiveEditorError raise)
        self.assertIsNotNone(editor._subprocess_runner)


if __name__ == "__main__":
    unittest.main()
