"""yule-llm-gateway — minimal central interface for LLM provider calls.

This package is a deliberately *minimal* seam: request/response/usage models, a
token-budget tracker, a prompt-cache metadata layer, and a thin pluggable
:class:`LLMGateway` dispatcher. It does NOT call real providers and does NOT
import agent internals — the real runners stay where they are and may register
themselves with the gateway later. See README.md for the migration TODO list.

Dependency rule: standard library only. MUST NOT import ``yule_orchestrator`` or
any ``apps/*`` code — the arrow always points the other way (app -> gateway).
"""

from __future__ import annotations

from .client import GatewayProvider, LLMGateway, ProviderNotRegistered
from .models import LLMRequest, LLMResponse, Message, TokenUsage
from .prompt_cache import CacheLookup, PromptCache, compute_cache_key
from .providers import (
    ProviderNotImplemented,
    build_claude_provider,
    build_codex_provider,
    build_echo_provider,
    build_gemini_provider,
    build_ollama_provider,
)
from .token_budget import BudgetExceededError, TokenBudget

__version__ = "0.1.0"

__all__ = (
    "__version__",
    # models
    "LLMRequest",
    "LLMResponse",
    "Message",
    "TokenUsage",
    # budget
    "TokenBudget",
    "BudgetExceededError",
    # cache
    "PromptCache",
    "CacheLookup",
    "compute_cache_key",
    # client
    "LLMGateway",
    "GatewayProvider",
    "ProviderNotRegistered",
    # providers
    "ProviderNotImplemented",
    "build_echo_provider",
    "build_claude_provider",
    "build_codex_provider",
    "build_gemini_provider",
    "build_ollama_provider",
)
