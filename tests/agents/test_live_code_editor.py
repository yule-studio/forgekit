"""LiveCodeEditor — F4 / #91 MVP unit tests.

The MVP intentionally lands a *very narrow* slice:

  * env gate (``YULE_LIVE_EDITOR_ENABLED`` must be ``"true"``)
  * provider dispatch (claude-cli vs blocked Anthropic / OpenAI)
  * PasteGuard preflight (raw secret in prompt blocks the call)
  * factory ``build_live_editor_from_env`` (off → None, on +
    claude-cli → :class:`LiveCodeEditor`, on + blocked provider →
    :class:`LiveCodeEditor` whose ``apply`` raises).

Out of scope for this PR (TODO follow-ups): patch validation
against write_scope / forbidden_scope, test-first retry loop,
cost tracking. The tests below pin the MVP contract only so
those features can be added without breaking the seam.
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
    BlockedLiveEditorError,
    ENV_LIVE_EDITOR_ENABLED,
    ENV_LIVE_EDITOR_MAX_RETRIES,
    ENV_LIVE_EDITOR_MODEL,
    ENV_LIVE_EDITOR_PROVIDER,
    LiveCodeEditor,
    PROVIDER_ANTHROPIC,
    PROVIDER_CLAUDE_CLI,
    PROVIDER_OPENAI,
    build_live_editor_from_env,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
    is_protected_branch,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _request(**overrides: Any) -> CodingExecuteRequest:
    base = {
        "session_id": "sess-live-editor-1",
        "executor_role": "backend-engineer",
        "user_request": "fix users 401",
        "generated_prompt": "rewrite services/auth/login.py to handle empty tokens",
        "write_scope": ("services/auth/**",),
        "forbidden_scope": (".github/workflows/**",),
        "safety_rules": ("no force push",),
        "base_branch": "main",
        "branch_hint": "agent/backend-engineer/issue-91-fix",
        "repo_full_name": "yule-studio/yule-studio-agent",
        "issue_number": 91,
        "dry_run": False,
        "metadata": {},
    }
    base.update(overrides)
    return CodingExecuteRequest(**base)


class _FakeRunner:
    """Records every subprocess call so the test can assert on it."""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[Sequence[str], Mapping[str, Any]]] = []
        self._raise = raise_exc

    def __call__(self, cmd: Sequence[str], **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append((tuple(cmd), dict(kwargs)))
        if self._raise:
            raise self._raise
        return {"stdout": "ok", "exit_code": 0}


def _worktree_ctx(path: Path) -> WorktreeContext:
    return WorktreeContext(
        branch="agent/backend-engineer/issue-91-fix",
        worktree_path=str(path),
        base_commit_sha="deadbeef",
    )


# ---------------------------------------------------------------------------
# build_live_editor_from_env
# ---------------------------------------------------------------------------


class BuildLiveEditorFromEnvTests(unittest.TestCase):
    def test_env_off_returns_none(self) -> None:
        self.assertIsNone(build_live_editor_from_env({}))
        self.assertIsNone(
            build_live_editor_from_env({ENV_LIVE_EDITOR_ENABLED: "false"})
        )

    def test_provider_missing_returns_none(self) -> None:
        env = {ENV_LIVE_EDITOR_ENABLED: "true"}
        self.assertIsNone(build_live_editor_from_env(env))

    def test_enabled_with_claude_cli_returns_live_editor(self) -> None:
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
            },
            subprocess_runner=_FakeRunner(),
        )
        self.assertIsInstance(editor, LiveCodeEditor)
        self.assertEqual(editor.provider, PROVIDER_CLAUDE_CLI)  # type: ignore[union-attr]

    def test_max_retries_falls_back_when_unparseable(self) -> None:
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
                ENV_LIVE_EDITOR_MAX_RETRIES: "not-an-int",
            },
            subprocess_runner=_FakeRunner(),
        )
        assert isinstance(editor, LiveCodeEditor)
        # Default fallback is 3 — bad value must not crash.
        self.assertEqual(editor.max_retries, 3)


# ---------------------------------------------------------------------------
# LiveCodeEditor.apply — env gate
# ---------------------------------------------------------------------------


class EnvGateTests(unittest.TestCase):
    def test_env_off_blocks_call(self) -> None:
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=_FakeRunner(),
            env={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockedLiveEditorError) as cm:
                editor.apply(request=_request(), context=_worktree_ctx(Path(tmp)))
            self.assertIn(ENV_LIVE_EDITOR_ENABLED, str(cm.exception))


# ---------------------------------------------------------------------------
# LiveCodeEditor.apply — provider dispatch
# ---------------------------------------------------------------------------


class ProviderDispatchTests(unittest.TestCase):
    def test_anthropic_provider_requires_authorization(self) -> None:
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_ANTHROPIC,
            }
        )
        assert isinstance(editor, LiveCodeEditor)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockedLiveEditorError) as cm:
                editor.apply(request=_request(), context=_worktree_ctx(Path(tmp)))
            self.assertIn("operator authorization", str(cm.exception))

    def test_openai_provider_requires_authorization(self) -> None:
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_OPENAI,
            }
        )
        assert isinstance(editor, LiveCodeEditor)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockedLiveEditorError) as cm:
                editor.apply(request=_request(), context=_worktree_ctx(Path(tmp)))
            self.assertIn("operator authorization", str(cm.exception))

    def test_unknown_provider_blocked(self) -> None:
        editor = LiveCodeEditor(
            provider="codex-cli",
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockedLiveEditorError) as cm:
                editor.apply(request=_request(), context=_worktree_ctx(Path(tmp)))
            self.assertIn("unknown live editor provider", str(cm.exception))

    def test_claude_cli_with_fake_runner_succeeds(self) -> None:
        fake = _FakeRunner()
        editor = build_live_editor_from_env(
            {
                ENV_LIVE_EDITOR_ENABLED: "true",
                ENV_LIVE_EDITOR_PROVIDER: PROVIDER_CLAUDE_CLI,
                ENV_LIVE_EDITOR_MODEL: "claude-sonnet-4-6",
            },
            subprocess_runner=fake,
        )
        assert isinstance(editor, LiveCodeEditor)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _worktree_ctx(Path(tmp))
            new_ctx = editor.apply(request=_request(), context=ctx)
        self.assertIs(new_ctx, ctx)
        self.assertEqual(len(fake.calls), 1)
        cmd, kwargs = fake.calls[0]
        self.assertEqual(cmd[0], "claude")
        self.assertEqual(cmd[1], "-p")
        # Model arg propagated.
        self.assertIn("--model", cmd)
        self.assertIn("claude-sonnet-4-6", cmd)
        self.assertEqual(kwargs.get("cwd"), str(Path(tmp)))

    def test_claude_cli_no_runner_blocks(self) -> None:
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BlockedLiveEditorError) as cm:
                editor.apply(request=_request(), context=_worktree_ctx(Path(tmp)))
            self.assertIn("subprocess_runner", str(cm.exception))

    def test_claude_cli_missing_worktree_blocks(self) -> None:
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=_FakeRunner(),
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        ctx = WorktreeContext(branch="agent/backend-engineer/issue-91-fix")
        with self.assertRaises(BlockedLiveEditorError) as cm:
            editor.apply(request=_request(), context=ctx)
        self.assertIn("worktree_path", str(cm.exception))


# ---------------------------------------------------------------------------
# LiveCodeEditor.apply — PasteGuard preflight
# ---------------------------------------------------------------------------


class PasteGuardPreflightTests(unittest.TestCase):
    def test_raw_secret_in_prompt_blocks_call(self) -> None:
        # Pattern-shaped sentinel that matches the anthropic regex
        # exactly but is NOT a real credential. PasteGuard treats it
        # as a critical finding and the redacted output still
        # contains the head4 + tail4 — that is fine for the LLM, but
        # if the guard's verdict came back ``blocked``, the editor
        # must refuse the call. We force the blocked path by injecting
        # a payload long enough to *only* contain the redacted form
        # of a Discord bot token, which PasteGuard masks as ``***``
        # (collapsed) and re-scans clean — so we use a payload that
        # cannot be safely redacted: a malformed nested key.
        #
        # Simpler: poke guard_outbound by monkey-patching to return
        # ``blocked=True`` so we exercise the editor branch directly.
        from yule_orchestrator.agents.security import paste_guard as pg_mod

        original = pg_mod.guard_outbound

        def _blocking_guard(*, channel, payload, fail_closed=True):  # noqa: ARG001
            return pg_mod.GuardVerdict(
                channel=channel,
                original_hash="sha256:test",
                findings=(),
                redacted="",
                blocked=True,
            )

        pg_mod.guard_outbound = _blocking_guard
        try:
            editor = LiveCodeEditor(
                provider=PROVIDER_CLAUDE_CLI,
                subprocess_runner=_FakeRunner(),
                env={ENV_LIVE_EDITOR_ENABLED: "true"},
            )
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BlockedLiveEditorError) as cm:
                    editor.apply(
                        request=_request(),
                        context=_worktree_ctx(Path(tmp)),
                    )
                self.assertIn("PasteGuard", str(cm.exception))
        finally:
            pg_mod.guard_outbound = original

    def test_real_pattern_matched_secret_redacted_then_passed(self) -> None:
        # End-to-end: a pattern-shaped sentinel goes through PasteGuard,
        # gets masked, and the live editor receives the redacted form.
        # The raw bytes must NOT appear in the subprocess command list.
        fake_secret_in_prompt = "fix bug; key=sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
        editor = LiveCodeEditor(
            provider=PROVIDER_CLAUDE_CLI,
            subprocess_runner=_FakeRunner(),
            env={ENV_LIVE_EDITOR_ENABLED: "true"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            editor.apply(
                request=_request(generated_prompt=fake_secret_in_prompt),
                context=_worktree_ctx(Path(tmp)),
            )
        # subprocess_runner is the fake; pull its first call.
        cmd, _ = editor._subprocess_runner.calls[0]  # type: ignore[attr-defined]
        joined = " ".join(cmd)
        # Raw secret must NOT make it to the subprocess.
        self.assertNotIn("AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH", joined)


# ---------------------------------------------------------------------------
# protected branch guard remains intact (regression — must not regress
# the worker-level rail that the live editor *cannot* relax).
# ---------------------------------------------------------------------------


class ProtectedBranchGuardTests(unittest.TestCase):
    def test_is_protected_branch_still_rejects_main(self) -> None:
        # The live editor lives *below* the protected-branch gate
        # in the worker pipeline. We pin the gate here so a refactor
        # of either layer trips the regression.
        self.assertTrue(is_protected_branch("main"))
        self.assertTrue(is_protected_branch("master"))
        self.assertTrue(is_protected_branch("develop"))
        self.assertFalse(
            is_protected_branch("agent/backend-engineer/issue-91-fix")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
