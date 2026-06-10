"""The single entry-point seam agents COULD adopt for LLM calls.

:class:`LLMGateway` is a thin dispatcher: callers register a provider callable
under a provider name, then call :meth:`generate` with an
:class:`~yule_llm_gateway.models.LLMRequest`. The gateway routes to the matching
provider, optionally consults a :class:`~yule_llm_gateway.prompt_cache.PromptCache`
for hit/miss metadata, and records token spend into an optional
:class:`~yule_llm_gateway.token_budget.TokenBudget`.

It deliberately does NOT call any real provider. The real runners
(``agents.runners.*`` / ``planning.ollama``) stay where they are; the intended
migration is to register them here as provider callables. Until then, the
built-in echo provider lets tests and dry-run wiring exercise the seam.
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol, runtime_checkable

from .models import LLMRequest, LLMResponse
from .prompt_cache import CacheLookup, PromptCache
from .providers.base import Provider, build_echo_provider
from .token_budget import TokenBudget


@runtime_checkable
class GatewayProvider(Protocol):
    """Structural type for a provider: a callable request -> response."""

    def __call__(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - protocol
        ...


class ProviderNotRegistered(KeyError):
    """Raised by :meth:`LLMGateway.generate` when no provider matches the request."""


class LLMGateway:
    """Minimal, pluggable dispatcher to registered provider callables.

    Usage::

        gateway = LLMGateway(budget=TokenBudget(total=10_000))
        gateway.register_provider("claude", my_claude_callable)
        response = gateway.generate(LLMRequest(provider="claude", model="...", prompt="..."))

    The gateway owns no provider logic itself — it only routes, and threads the
    optional budget + cache bookkeeping around the call.
    """

    def __init__(
        self,
        *,
        budget: Optional[TokenBudget] = None,
        cache: Optional[PromptCache] = None,
    ) -> None:
        self._providers: Dict[str, Provider] = {}
        self.budget = budget
        self.cache = cache

    def register_provider(self, name: str, provider: Provider) -> "LLMGateway":
        """Register *provider* under *name* (overwrites an existing one). Chains."""

        self._providers[name] = provider
        return self

    def register_echo(self, name: str) -> "LLMGateway":
        """Convenience: register the deterministic echo provider under *name*."""

        return self.register_provider(name, build_echo_provider())

    def has_provider(self, name: str) -> bool:
        return name in self._providers

    @property
    def providers(self) -> Dict[str, Provider]:
        return dict(self._providers)

    def last_cache_lookup(self) -> Optional[CacheLookup]:
        return self.cache.last() if self.cache is not None else None

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Route *request* to its registered provider and return the response.

        Side effects (both optional, both best-effort observability):
          * If a :class:`PromptCache` is attached, a hit/miss lookup is recorded
            before dispatch (the gateway still calls the provider — this is a
            metadata layer, not a real response cache).
          * If a :class:`TokenBudget` is attached, the response usage is charged
            to it (raising :class:`~yule_llm_gateway.token_budget.BudgetExceededError`
            when over budget).
        """

        provider = self._providers.get(request.provider)
        if provider is None:
            raise ProviderNotRegistered(
                f"no provider registered for '{request.provider}'; "
                f"registered={sorted(self._providers)}"
            )

        if self.cache is not None:
            self.cache.lookup(request)

        response = provider(request)

        if self.budget is not None:
            self.budget.charge(response.usage)

        return response


__all__ = (
    "LLMGateway",
    "GatewayProvider",
    "ProviderNotRegistered",
)
