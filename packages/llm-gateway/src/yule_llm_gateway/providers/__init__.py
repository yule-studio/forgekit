"""Thin provider adapters / placeholders for the LLM gateway.

Each module here documents *where the real runner lives* and exposes a
``build_*_provider()`` factory returning a callable
``(LLMRequest) -> LLMResponse``. The default callables are **stubs**: they raise
:class:`ProviderNotImplemented` so a caller is never silently given fake output,
EXCEPT the echo provider which returns a deterministic response for tests and
dry-run wiring.

DELIBERATE non-goal: these adapters do NOT import the real runners
(``yule_engineering.agents.runners.*`` / ``yule_engineering.planning.ollama``).
Importing them would couple the gateway package to app internals and risk import
cycles. The migration path is to have those runners *register* themselves with
:class:`yule_llm_gateway.client.LLMGateway`, not the other way around.
"""

from __future__ import annotations

from .base import ProviderNotImplemented, build_echo_provider
from .claude import build_claude_provider
from .codex import build_codex_provider
from .gemini import build_gemini_provider
from .ollama import build_ollama_provider

__all__ = (
    "ProviderNotImplemented",
    "build_echo_provider",
    "build_claude_provider",
    "build_codex_provider",
    "build_gemini_provider",
    "build_ollama_provider",
)
