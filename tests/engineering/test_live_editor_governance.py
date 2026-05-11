"""Live LLM editor governance regression — F4 / #91 hard-rail gate.

Mirrors :mod:`tests.engineering.test_paste_guard_governance` posture:
one suite pinning the hard rails of the live editor so a single
condition flip / rename / silent env default change trips a clearly
named test.

Rails pinned (D-73-10 cost-budget gate + #88 PasteGuard fail-closed):

  1. ``build_live_editor_from_env({})`` returns ``None`` —
     **default-off**. Merging this PR must NOT auto-enable the live
     editor; the operator has to flip ``YULE_LIVE_EDITOR_ENABLED``
     in ``.env.local`` to opt in.

  2. ``provider=anthropic`` / ``provider=openai`` raise
     :class:`BlockedLiveEditorError` on ``apply`` even when the env
     flag is ``"true"``. The Anthropic / OpenAI SDKs require
     explicit operator authorization (D-73-10) and live wiring
     lands in a separate PR with cost-budget review.

  3. ``LiveCodeEditor`` consults
     :func:`yule_orchestrator.agents.security.paste_guard.guard_outbound`
     on every call. A blocked verdict raises BlockedLiveEditorError.

  4. ``coding_executor_worker.is_protected_branch`` continues to flag
     ``main`` / ``master`` / ``develop`` so the live editor sits
     below an unchangeable protected-branch gate.

  5. ``RecordOnlyCodeEditor`` remains importable and unchanged — the
     fallback path stays intact when env is off.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.coding_executor_live import (
    BlockedLiveEditorError,
    ENV_LIVE_EDITOR_ENABLED,
    ENV_LIVE_EDITOR_PROVIDER,
    LiveCodeEditor,
    PROVIDER_ANTHROPIC,
    PROVIDER_CLAUDE_CLI,
    PROVIDER_OPENAI,
    RecordOnlyCodeEditor,
    build_live_editor_from_env,
)
from yule_orchestrator.agents.job_queue.coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
    is_protected_branch,
)


def _request() -> CodingExecuteRequest:
    return CodingExecuteRequest(
        session_id="sess-gov-91",
        executor_role="backend-engineer",
        user_request="x",
        generated_prompt="rewrite services/auth/login.py",
        write_scope=("services/auth/**",),
        forbidden_scope=(".github/workflows/**",),
        safety_rules=("no force push",),
        base_branch="main",
        branch_hint="agent/backend-engineer/issue-91",
        repo_full_name="yule-studio/yule-studio-agent",
        issue_number=91,
        dry_run=False,
        metadata={},
    )


def _ctx(path: Path) -> WorktreeContext:
    return WorktreeContext(
        branch="agent/backend-engineer/issue-91",
        worktree_path=str(path),
        base_commit_sha="cafe",
    )


class LiveEditorGovernanceTests(unittest.TestCase):
    # 1 — Default OFF.
    def test_default_env_returns_none(self) -> None:
        self.assertIsNone(build_live_editor_from_env({}))
        # Sentinel values that look truthy but are not "true":
        for falsey in ("false", "0", "off", "no", ""):
            self.assertIsNone(
                build_live_editor_from_env({ENV_LIVE_EDITOR_ENABLED: falsey}),
                msg=f"value={falsey!r} must NOT auto-enable",
            )

    # 2 — Anthropic / OpenAI providers must block on apply.
    def test_blocked_providers_raise_on_apply(self) -> None:
        for provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENAI):
            editor = build_live_editor_from_env(
                {
                    ENV_LIVE_EDITOR_ENABLED: "true",
                    ENV_LIVE_EDITOR_PROVIDER: provider,
                }
            )
            assert isinstance(editor, LiveCodeEditor)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BlockedLiveEditorError) as cm:
                    editor.apply(request=_request(), context=_ctx(Path(tmp)))
                self.assertIn("operator authorization", str(cm.exception))

    # 3 — PasteGuard preflight is mandatory.
    def test_paste_guard_blocked_verdict_refuses_call(self) -> None:
        from yule_orchestrator.agents.security import paste_guard as pg_mod

        original = pg_mod.guard_outbound

        def _blocking_guard(*, channel, payload, fail_closed=True):  # noqa: ARG001
            return pg_mod.GuardVerdict(
                channel=channel,
                original_hash="sha256:x",
                findings=(),
                redacted="",
                blocked=True,
            )

        pg_mod.guard_outbound = _blocking_guard
        try:
            editor = LiveCodeEditor(
                provider=PROVIDER_CLAUDE_CLI,
                subprocess_runner=lambda *a, **k: {"ok": True},
                env={ENV_LIVE_EDITOR_ENABLED: "true"},
            )
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BlockedLiveEditorError):
                    editor.apply(request=_request(), context=_ctx(Path(tmp)))
        finally:
            pg_mod.guard_outbound = original

    # 4 — protected branch gate intact.
    def test_protected_branch_guard_unchanged(self) -> None:
        for name in ("main", "master", "develop", "dev", "prod", "release"):
            self.assertTrue(
                is_protected_branch(name), msg=f"{name} must stay protected"
            )

    # 5 — RecordOnly fallback remains intact.
    def test_record_only_editor_remains_importable(self) -> None:
        self.assertTrue(callable(getattr(RecordOnlyCodeEditor, "apply", None)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
