"""Shared building blocks for provider stubs.

Defines the :class:`ProviderNotImplemented` sentinel error and an *echo*
provider used by tests and dry-run wiring. The echo provider is the only
stub that returns a real :class:`LLMResponse`; every real provider stub raises
:class:`ProviderNotImplemented` until its runner is migrated in.
"""

from __future__ import annotations

from typing import Callable

from ..models import LLMRequest, LLMResponse, TokenUsage

# A provider is just a callable from a request to a response. This keeps the
# seam minimal — a real adapter is registered as a closure over its runner.
Provider = Callable[[LLMRequest], LLMResponse]


class ProviderNotImplemented(NotImplementedError):
    """Raised by a stub provider that has no real backend wired up yet."""


def _stub_provider(provider_name: str, runner_reference: str) -> Provider:
    """Build a provider callable that refuses to fabricate output.

    *runner_reference* points at the real call site so the error message tells
    the operator exactly where the implementation should come from.
    """

    def _call(request: LLMRequest) -> LLMResponse:
        raise ProviderNotImplemented(
            f"{provider_name} provider is a placeholder in yule-llm-gateway; "
            f"the real call lives in {runner_reference}. "
            "Register a concrete provider via LLMGateway.register_provider(...)."
        )

    return _call


def build_echo_provider() -> Provider:
    """Return a deterministic provider that echoes the prompt.

    Used by tests and by the dry-run path to exercise the gateway seam without
    contacting any backend. Token usage is a trivial whitespace word count so a
    :class:`~yule_llm_gateway.token_budget.TokenBudget` has something to record.
    """

    def _call(request: LLMRequest) -> LLMResponse:
        if request.prompt is not None:
            text = request.prompt
        else:
            text = "\n".join(m.content for m in request.messages)
        input_tokens = len(text.split())
        echoed = f"[echo:{request.provider}] {text}"
        output_tokens = len(echoed.split())
        return LLMResponse(
            text=echoed,
            model=request.model,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            raw={"echo": True, "provider": request.provider},
        )

    return _call


__all__ = (
    "Provider",
    "ProviderNotImplemented",
    "build_echo_provider",
)
