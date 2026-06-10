"""Issue #73 Round 2 governance regression — hard-rail integrity gate.

The Round 2 commits add live wiring across 4 areas (coding executor,
real classifier, runtime auto-spawn, CI retry loop). Each area carries
hard rails that, if silently regressed, would let the runtime do
something the user explicitly forbade in the Round 2 brief.

This test pulls all five rails into one suite so a single rename /
condition flip / docs drift trips a clearly-named test:

  1. ``RecordOnlyCodeEditor.apply_edits`` never touches the source
     tree (the LLM editor block is a hard rail until operator authorization).
  2. ``is_protected_branch`` continues to flag ``main`` / ``master`` /
     ``develop`` / ``production`` so the executor cannot push there.
  3. ``build_classifier_from_env`` does NOT auto-enable any provider
     on key detection alone — the explicit
     ``YULE_DECISION_<provider>_ENABLED=true`` flag is required.
  4. ``_build_engineering_profile(env={})`` keeps eng-coding-executor
     opt-in (auto_spawn=False) — runtime up will not spawn it without
     the explicit ``YULE_CODING_EXECUTOR_AUTOSPAWN`` flag.
  5. ``decide_retry`` flips a CI failure to ``blocked`` once
     ``attempts >= policy.max_attempts`` — no infinite retry.
  6. The Round 2 task-log mirror documents commits 6~10 (so the
     governance audit trail does not drift away from the code).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.decision.classifier_factory import (
    ENV_ANTHROPIC_API_KEY,
    ENV_ANTHROPIC_API_KEY_ALT,
    ENV_OLLAMA_ENABLED,
    ENV_OLLAMA_ENDPOINT,
    ENV_OPENAI_API_KEY,
    build_classifier_from_env,
)
from yule_engineering.agents.job_queue.ci_status import (
    CI_FAILURE,
    CIRetryPolicy,
    CIStatus,
    RetryAttemptLog,
    decide_retry,
)
from yule_engineering.agents.job_queue.coding_executor_live import (
    RecordOnlyCodeEditor,
)
from yule_engineering.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
    is_protected_branch,
)
from yule_engineering.runtime.services import (
    ENV_CODING_EXECUTOR_AUTOSPAWN,
    build_engineering_profile,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_LOG = (
    _REPO_ROOT
    / "notes"
    / "vault-mirror"
    / "10-projects"
    / "yule-studio-agent"
    / "task-logs"
    / "task-log-tech-lead-runtime-loop-issue-73.md"
)


def _request() -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="sess-gov-1",
        executor_role="backend-engineer",
        user_request="hard-rail check",
        generated_prompt="(prompt)",
        write_scope=("services/auth/**",),
        forbidden_scope=(".github/workflows/**",),
        safety_rules=("no force push",),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-99-fix",
        repo_full_name="yule-studio/yule-studio-agent",
        issue_number=99,
        dry_run=False,
        metadata={},
    )


class CodeEditorRecordOnlyRailTests(unittest.TestCase):
    """LLM editor remains blocked — RecordOnlyCodeEditor is the live stand-in."""

    def test_apply_writes_plan_only_and_never_touches_existing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            sentinel = worktree / "src.py"
            sentinel.write_text("# original\n", encoding="utf-8")
            editor = RecordOnlyCodeEditor()
            ctx = WorktreeContext(
                branch="agent/backend-engineer/issue-99-fix",
                worktree_path=str(worktree),
                base_commit_sha="deadbeef",
            )

            new_ctx = editor.apply(request=_request(), context=ctx)

            # Hard rail: existing source bytes are unchanged.
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "# original\n"
            )
            # The single edited file must be the plan markdown — no
            # source files (e.g. services/auth/**) are touched.
            self.assertEqual(len(new_ctx.edited_files), 1)
            plan_path = worktree / new_ctx.edited_files[0]
            self.assertTrue(plan_path.is_file())
            body = plan_path.read_text(encoding="utf-8")
            self.assertIn("coding-executor plan", body)
            self.assertIn("RecordOnlyCodeEditor", body)
            # No surprise files outside the plan dir.
            edited_rel = new_ctx.edited_files[0]
            self.assertTrue(edited_rel.startswith("runs/"))


class ProtectedBranchRailTests(unittest.TestCase):
    def test_known_protected_branches_remain_blocked(self) -> None:
        # Names guarded by exact match: main / master / develop / dev /
        # prod / release. Plus prefix-matched: release/* and hotfix/*.
        for branch in (
            "main",
            "master",
            "develop",
            "dev",
            "prod",
            "release",
            "release/2026-05",
            "hotfix/critical",
        ):
            with self.subTest(branch=branch):
                self.assertTrue(
                    is_protected_branch(branch),
                    f"{branch} must be blocked from coding_execute push",
                )

    def test_feature_branches_are_not_protected(self) -> None:
        for branch in (
            "feature/tech-lead-runtime-loop",
            "fix/lint-bug",
            "chore/docs",
        ):
            with self.subTest(branch=branch):
                self.assertFalse(is_protected_branch(branch))

    def test_empty_branch_treated_as_protected(self) -> None:
        # Defensive: empty / None-like input must not bypass the rail.
        self.assertTrue(is_protected_branch(""))


class ClassifierAuthorizationRailTests(unittest.TestCase):
    def test_anthropic_key_alone_does_not_enable(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_ANTHROPIC_API_KEY: "sk-ant-test"}
        )
        self.assertEqual(resolution.provider, "none")
        self.assertIsNone(resolution.classifier)

    def test_claude_alt_key_alone_does_not_enable(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_ANTHROPIC_API_KEY_ALT: "sk-ant-alias"}
        )
        self.assertEqual(resolution.provider, "none")

    def test_openai_key_alone_does_not_enable(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_OPENAI_API_KEY: "sk-openai-test"}
        )
        self.assertEqual(resolution.provider, "none")

    def test_ollama_endpoint_alone_does_not_enable(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_OLLAMA_ENDPOINT: "http://localhost:11434"}
        )
        self.assertEqual(resolution.provider, "none")

    def test_ollama_enabled_without_endpoint_does_not_enable(self) -> None:
        resolution = build_classifier_from_env(
            env={ENV_OLLAMA_ENABLED: "true"}
        )
        self.assertEqual(resolution.provider, "none")


class CodingExecutorAutoSpawnRailTests(unittest.TestCase):
    def test_default_env_keeps_executor_opt_in(self) -> None:
        profile = build_engineering_profile(env={})
        spec = next(
            s for s in profile if s.service_id == "eng-coding-executor"
        )
        self.assertFalse(spec.auto_spawn)

    def test_unrelated_envs_do_not_flip_executor_on(self) -> None:
        # Even if every other yule-related env is set, the executor
        # remains opt-in until the explicit flag arrives.
        env = {
            "YULE_GITHUB_APP_ID": "123456",
            "YULE_GITHUB_APP_INSTALLATION_ID": "789",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-openai-test",
            "OLLAMA_ENDPOINT": "http://localhost:11434",
        }
        profile = build_engineering_profile(env=env)
        spec = next(
            s for s in profile if s.service_id == "eng-coding-executor"
        )
        self.assertFalse(spec.auto_spawn)

    def test_explicit_flag_flips_executor_on(self) -> None:
        profile = build_engineering_profile(
            env={ENV_CODING_EXECUTOR_AUTOSPAWN: "true"}
        )
        spec = next(
            s for s in profile if s.service_id == "eng-coding-executor"
        )
        self.assertTrue(spec.auto_spawn)


class CIRetryGuardRailTests(unittest.TestCase):
    def _failure(self) -> CIStatus:
        return CIStatus(
            pr_number=42,
            head_sha="abc",
            conclusion=CI_FAILURE,
            failing_runs=("test",),
        )

    def test_max_attempts_reached_blocks(self) -> None:
        verdict = decide_retry(
            status=self._failure(),
            log=RetryAttemptLog(pr_number=42, attempts=3),
            policy=CIRetryPolicy(max_attempts=3),
        )
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.completion_status, "blocked")

    def test_above_max_attempts_blocks(self) -> None:
        # Defensive — even if attempt counter somehow exceeds max,
        # we still escalate (no negative-budget infinite retry).
        verdict = decide_retry(
            status=self._failure(),
            log=RetryAttemptLog(pr_number=42, attempts=99),
            policy=CIRetryPolicy(max_attempts=3),
        )
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.completion_status, "blocked")

    def test_zero_max_attempts_blocks_immediately(self) -> None:
        verdict = decide_retry(
            status=self._failure(),
            log=RetryAttemptLog(pr_number=42, attempts=0),
            policy=CIRetryPolicy(max_attempts=0),
        )
        self.assertFalse(verdict.should_retry)
        self.assertEqual(verdict.completion_status, "blocked")


class TaskLogAuditTrailTests(unittest.TestCase):
    def test_round2_section_is_present(self) -> None:
        text = _TASK_LOG.read_text(encoding="utf-8")
        self.assertIn("Round 2 — 종료 시점 갱신", text)

    def test_round2_section_lists_each_commit(self) -> None:
        text = _TASK_LOG.read_text(encoding="utf-8")
        for marker in (
            "commit-6",
            "commit-7",
            "commit-8",
            "commit-9",
            "commit-10",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text, f"task-log missing {marker}")

    def test_round2_section_documents_hard_rails(self) -> None:
        text = _TASK_LOG.read_text(encoding="utf-8")
        for keyword in (
            "is_protected_branch",
            "RecordOnlyCodeEditor",
            "YULE_CODING_EXECUTOR_AUTOSPAWN",
            "max_attempts",
        ):
            with self.subTest(keyword=keyword):
                self.assertIn(
                    keyword,
                    text,
                    f"task-log Round 2 missing hard-rail keyword {keyword}",
                )


if __name__ == "__main__":
    unittest.main()
