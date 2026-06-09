"""Gemini provider placeholder.

Real call site (do NOT import from here):
    src/yule_orchestrator/agents/runners/gemini.py
        ``GeminiRunner`` — wraps Google's ``gemini`` CLI (long-context / advise).

This stub documents the seam; ``build_gemini_provider`` returns a callable that
raises :class:`ProviderNotImplemented` until the runner is registered with the
gateway. See packages/llm-gateway/README.md for the migration TODO.
"""

from __future__ import annotations

from .base import Provider, _stub_provider

_RUNNER_REFERENCE = "src/yule_orchestrator/agents/runners/gemini.py (GeminiRunner)"


def build_gemini_provider() -> Provider:
    return _stub_provider("gemini", _RUNNER_REFERENCE)


__all__ = ("build_gemini_provider",)
