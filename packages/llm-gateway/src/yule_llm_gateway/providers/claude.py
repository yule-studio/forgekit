"""Claude provider placeholder.

Real call site (do NOT import from here):
    src/yule_orchestrator/agents/runners/claude_code.py
        ``ClaudeCodeRunner`` — wraps the local ``claude`` CLI.

This stub documents the seam; ``build_claude_provider`` returns a callable that
raises :class:`ProviderNotImplemented` until the runner is registered with the
gateway. See packages/llm-gateway/README.md for the migration TODO.
"""

from __future__ import annotations

from .base import Provider, _stub_provider

_RUNNER_REFERENCE = "src/yule_orchestrator/agents/runners/claude_code.py (ClaudeCodeRunner)"


def build_claude_provider() -> Provider:
    return _stub_provider("claude", _RUNNER_REFERENCE)


__all__ = ("build_claude_provider",)
