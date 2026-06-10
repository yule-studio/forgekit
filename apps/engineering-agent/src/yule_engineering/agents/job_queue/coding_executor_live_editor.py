"""F4 / #91 — Live LLM editor MVP for the coding executor.

Split out of :mod:`coding_executor_live` (responsibility: *live
runner — LLM editor seam*). Behavior-preserving move; the original
module re-exports every public symbol so importers stay unchanged.

Scope of this seam (intentionally minimal):

  * :class:`CodeEditPort` — Protocol the worker can swap in place of
    :class:`RecordOnlyCodeEditor`.
  * :class:`BlockedLiveEditorError` — single exception type raised
    when env gates / operator authorization / PasteGuard refuses
    the call.
  * :class:`LiveCodeEditor` — env-gated wrapper that:
      1. Hard rail: if ``YULE_LIVE_EDITOR_ENABLED != "true"`` the
         editor blocks immediately. This stays default-off even
         after the PR lands — operator must flip the flag.
      2. PasteGuard preflight on the outbound prompt; ``blocked``
         → :class:`BlockedLiveEditorError`.
      3. Provider dispatch:
           ``claude-cli`` → subprocess call (default impl
           attempts ``import subprocess`` only; the worker may
           inject a fake runner under test).
           ``anthropic`` / ``openai`` → blocked stub (operator
           authorization + cost-budget gate, D-73-10).

TODO (follow-up PRs, deliberately out of scope here):

  * patch validation against write_scope / forbidden_scope
  * test-first retry loop (max_retries env exposed but unused)
  * cost tracking + per-session budget enforcement
  * Anthropic / OpenAI SDK wiring (operator authorization gate)

Hard rails enforced *in this PR* (regression-tested):

  * Default OFF — ``build_live_editor_from_env({})`` returns None.
  * Anthropic / OpenAI providers raise BlockedLiveEditorError.
  * PasteGuard fail-closed — raw secret in prompt blocks the call.
  * protected branch guard remains via
    :func:`coding_executor_worker.is_protected_branch` (not
    duplicated here; the worker invokes it before the editor).
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Protocol

from .coding_executor_worker import (
    CodingExecuteRequest,
    WorktreeContext,
)


ENV_LIVE_EDITOR_ENABLED: str = "YULE_LIVE_EDITOR_ENABLED"
ENV_LIVE_EDITOR_PROVIDER: str = "YULE_LIVE_EDITOR_PROVIDER"
ENV_LIVE_EDITOR_MODEL: str = "YULE_LIVE_EDITOR_MODEL"
ENV_LIVE_EDITOR_MAX_RETRIES: str = "YULE_LIVE_EDITOR_MAX_RETRIES"

PROVIDER_CLAUDE_CLI: str = "claude-cli"
PROVIDER_ANTHROPIC: str = "anthropic"
PROVIDER_OPENAI: str = "openai"

_DEFAULT_LIVE_EDITOR_MODEL: str = "claude-sonnet-4-6"
_DEFAULT_LIVE_EDITOR_MAX_RETRIES: int = 3


class BlockedLiveEditorError(RuntimeError):
    """Raised when :class:`LiveCodeEditor` refuses to execute a call.

    ``reason`` is operator-facing: env OFF, provider not authorized
    (anthropic / openai blocked stub), PasteGuard verdict blocked,
    or runtime resource missing (e.g. ``claude`` CLI not on PATH).

    The exception never carries the raw outbound prompt — callers
    log ``str(exc)`` directly without leaking the LLM input.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CodeEditPort(Protocol):
    """Protocol the worker depends on for the editor seam.

    :class:`RecordOnlyCodeEditor` and :class:`LiveCodeEditor` both
    satisfy this — the build factory picks one based on env. The
    contract is intentionally narrow so future implementations
    (e.g. codex CLI, GitHub Copilot CLI) can slot in unchanged.
    """

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:  # pragma: no cover - Protocol
        ...


class LiveCodeEditor:
    """Env-gated live LLM editor — MVP (claude-cli only).

    The constructor never reads env directly; use
    :func:`build_live_editor_from_env` so the env contract stays in
    one place and tests can construct the editor with explicit
    arguments.

    Provider matrix (MVP):

      * ``claude-cli`` — shells out to ``claude -p <prompt>`` via
        the injected ``subprocess_runner`` (default attempts a
        local ``subprocess.run`` call; under test the worker
        passes a fake). The default-off env flag and the
        PasteGuard preflight gate every call.
      * ``anthropic`` / ``openai`` — raises
        :class:`BlockedLiveEditorError` with reason
        ``"requires operator authorization"``. This keeps the
        D-73-10 cost-budget gate intact: live SDK wiring lands
        in a separate PR after operator sign-off.

    TODO (follow-up PRs): patch validation against write_scope,
    test-first retry loop, cost tracking.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str = _DEFAULT_LIVE_EDITOR_MODEL,
        max_retries: int = _DEFAULT_LIVE_EDITOR_MAX_RETRIES,
        subprocess_runner: Optional[Any] = None,
        http_poster: Optional[Any] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.max_retries = max_retries
        self._subprocess_runner = subprocess_runner
        self._http_poster = http_poster
        # Snapshot env so re-running apply does not silently flip
        # behaviour if the operator flips the flag mid-pipeline.
        self._env: Mapping[str, str] = dict(env) if env is not None else dict(os.environ)

    def apply(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
    ) -> WorktreeContext:
        # Hard rail 1 — env OFF default.
        if (self._env.get(ENV_LIVE_EDITOR_ENABLED) or "").strip().lower() != "true":
            raise BlockedLiveEditorError(
                f"{ENV_LIVE_EDITOR_ENABLED} != 'true' — live editor disabled"
            )

        # Hard rail 2 — PasteGuard preflight on the outbound prompt.
        # Imported lazily so the module stays importable in
        # environments that strip the security subpackage (e.g.
        # the planning-agent worker that never touches LLM I/O).
        from yule_security.paste_guard import (
            OutboundChannel,
            guard_outbound,
        )

        verdict = guard_outbound(
            channel=OutboundChannel.LLM,
            payload=request.generated_prompt or "",
        )
        if verdict.blocked:
            raise BlockedLiveEditorError(
                "PasteGuard blocked outbound prompt — refusing live LLM call"
            )

        # Hard rail 3 — provider dispatch.
        if self.provider == PROVIDER_CLAUDE_CLI:
            return self._apply_via_claude_cli(
                request=request,
                context=context,
                redacted_prompt=verdict.redacted,
            )
        if self.provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENAI):
            raise BlockedLiveEditorError(
                f"provider={self.provider} requires operator authorization "
                "(D-73-10 cost-budget gate)"
            )
        raise BlockedLiveEditorError(
            f"unknown live editor provider: {self.provider!r}"
        )

    # ------------------------------------------------------------------
    # Provider — claude CLI
    # ------------------------------------------------------------------

    def _apply_via_claude_cli(
        self,
        *,
        request: CodingExecuteRequest,
        context: WorktreeContext,
        redacted_prompt: str,
    ) -> WorktreeContext:
        if not context.worktree_path:
            raise BlockedLiveEditorError(
                "LiveCodeEditor requires a worktree_path in context"
            )

        runner = self._subprocess_runner
        if runner is None:
            # Default impl: best-effort attempt at locating the
            # ``claude`` binary. We intentionally do *not* fall back
            # to ``subprocess.run`` here — operators wire the runner
            # via :class:`ClaudeSubprocessAdapter` (separate PR).
            try:
                import subprocess as _subprocess  # noqa: F401 — import-only probe
            except Exception as exc:  # pragma: no cover - defensive
                raise BlockedLiveEditorError(
                    f"claude-cli runner unavailable: {type(exc).__name__}"
                ) from exc
            raise BlockedLiveEditorError(
                "claude-cli runner not injected — operator must wire "
                "subprocess_runner before enabling live editor"
            )

        # Pass the *redacted* payload — never the raw prompt. The
        # redaction is round-trip safe (head4 + mask + tail4) so the
        # LLM still has enough context to act, but a leaked secret
        # in the prompt cannot reach the network.
        cmd = ("claude", "-p", redacted_prompt, "--model", self.model)
        result = runner(cmd, cwd=context.worktree_path)
        # The runner is operator-defined; we accept any truthy
        # return shape. The MVP only verifies the call did not
        # raise. Patch validation / file diffing lands in a
        # follow-up PR (TODO).
        _ = result
        return context


def build_live_editor_from_env(
    env: Mapping[str, str],
    *,
    http_poster: Optional[Any] = None,
    subprocess_runner: Optional[Any] = None,
) -> Optional[CodeEditPort]:
    """Construct a :class:`LiveCodeEditor` from env, or return ``None``.

    Returns ``None`` (NOT an error) when:

      * ``YULE_LIVE_EDITOR_ENABLED`` is unset / not ``"true"``.
      * ``YULE_LIVE_EDITOR_PROVIDER`` is unset / empty.

    The worker treats ``None`` as "fall back to RecordOnly" so the
    pipeline stays exercisable end-to-end even with the live editor
    completely off. When the env says ON but the provider is
    ``anthropic`` / ``openai``, the returned editor still raises
    :class:`BlockedLiveEditorError` on ``apply`` — that is the
    intended D-73-10 cost-budget gate.

    TODO (follow-up PRs): validate model id against an allow-list,
    surface availability via :class:`LiveExecutorAvailability`.
    """

    if (env.get(ENV_LIVE_EDITOR_ENABLED) or "").strip().lower() != "true":
        return None

    provider = (env.get(ENV_LIVE_EDITOR_PROVIDER) or "").strip().lower()
    if not provider:
        return None

    model = (env.get(ENV_LIVE_EDITOR_MODEL) or "").strip() or _DEFAULT_LIVE_EDITOR_MODEL
    max_retries_raw = (env.get(ENV_LIVE_EDITOR_MAX_RETRIES) or "").strip()
    try:
        max_retries = int(max_retries_raw) if max_retries_raw else _DEFAULT_LIVE_EDITOR_MAX_RETRIES
    except ValueError:
        max_retries = _DEFAULT_LIVE_EDITOR_MAX_RETRIES

    return LiveCodeEditor(
        provider=provider,
        model=model,
        max_retries=max_retries,
        subprocess_runner=subprocess_runner,
        http_poster=http_poster,
        env=env,
    )
