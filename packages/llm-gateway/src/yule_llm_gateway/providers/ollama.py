"""Ollama provider placeholder.

Real call site (do NOT import from here):
    src/yule_orchestrator/planning/ollama.py
        ``generate_ollama_text`` / ``generate_human_briefing`` — HTTP POST to the
        local Ollama ``/api/generate`` endpoint.
    src/yule_orchestrator/planning/ollama_config.py
        ``OllamaPlanningConfig`` / ``OllamaConversationConfig`` — env-driven config.

This stub documents the seam; ``build_ollama_provider`` returns a callable that
raises :class:`ProviderNotImplemented` until the runner is registered with the
gateway. See packages/llm-gateway/README.md for the migration TODO.
"""

from __future__ import annotations

from .base import Provider, _stub_provider

_RUNNER_REFERENCE = "src/yule_orchestrator/planning/ollama.py (generate_ollama_text)"


def build_ollama_provider() -> Provider:
    return _stub_provider("ollama", _RUNNER_REFERENCE)


__all__ = ("build_ollama_provider",)
