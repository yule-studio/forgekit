"""Codex provider placeholder.

Real call site (do NOT import from here):
    src/yule_orchestrator/agents/runners/codex.py
        ``CodexRunner`` — wraps the OpenAI ``codex`` CLI (advise / review / patch).
    src/yule_orchestrator/agents/runners/bootstrap.py
        ``build_role_runner_candidates`` — env-driven runner wiring (claude/codex/ollama).

This stub documents the seam; ``build_codex_provider`` returns a callable that
raises :class:`ProviderNotImplemented` until the runner is registered with the
gateway. See packages/llm-gateway/README.md for the migration TODO.
"""

from __future__ import annotations

from .base import Provider, _stub_provider

_RUNNER_REFERENCE = "src/yule_orchestrator/agents/runners/codex.py (CodexRunner)"


def build_codex_provider() -> Provider:
    return _stub_provider("codex", _RUNNER_REFERENCE)


__all__ = ("build_codex_provider",)
