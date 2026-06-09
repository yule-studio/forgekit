"""Smoke tests for the minimal yule-llm-gateway surface."""

from __future__ import annotations

import pytest

from yule_llm_gateway import (
    BudgetExceededError,
    LLMGateway,
    LLMRequest,
    LLMResponse,
    Message,
    PromptCache,
    ProviderNotImplemented,
    ProviderNotRegistered,
    TokenBudget,
    TokenUsage,
    build_claude_provider,
    build_echo_provider,
    compute_cache_key,
)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_llm_request_construction_and_roundtrip():
    req = LLMRequest(
        provider="claude",
        model="claude-x",
        prompt="hello",
        max_tokens=128,
        temperature=0.4,
        metadata={"task_id": "T1"},
    )
    assert req.provider == "claude"
    assert req.prompt == "hello"
    assert LLMRequest.from_dict(req.to_dict()) == req


def test_llm_request_with_messages():
    req = LLMRequest(
        provider="gemini",
        model="g",
        messages=(Message("system", "be terse"), Message("user", "hi")),
    )
    restored = LLMRequest.from_dict(req.to_dict())
    assert restored.messages == req.messages


def test_token_usage_total_derived():
    usage = TokenUsage(input_tokens=10, output_tokens=5)
    assert usage.total == 15
    # explicit total wins
    assert TokenUsage(input_tokens=1, output_tokens=1, total=99).total == 99


def test_llm_response_roundtrip():
    resp = LLMResponse(
        text="out",
        model="m",
        usage=TokenUsage(input_tokens=3, output_tokens=4),
        raw={"finish": "stop"},
    )
    assert LLMResponse.from_dict(resp.to_dict()) == resp


# ---------------------------------------------------------------------------
# token budget
# ---------------------------------------------------------------------------


def test_token_budget_arithmetic():
    budget = TokenBudget(total=100)
    assert budget.remaining == 100
    budget.record(TokenUsage(input_tokens=30, output_tokens=10))
    assert budget.spent == 40
    assert budget.remaining == 60
    assert len(budget.history) == 1


def test_token_budget_over_budget_charge_raises_and_preserves_state():
    budget = TokenBudget(total=50)
    budget.charge(TokenUsage(total=40))
    assert budget.would_exceed(TokenUsage(total=20)) is True
    with pytest.raises(BudgetExceededError):
        budget.charge(TokenUsage(total=20))
    # rejected spend is NOT recorded
    assert budget.spent == 40
    assert budget.remaining == 10


def test_token_budget_unlimited():
    budget = TokenBudget()  # total == 0 -> unlimited
    assert budget.unlimited is True
    assert budget.remaining == -1
    assert budget.would_exceed(TokenUsage(total=10**9)) is False
    budget.charge(TokenUsage(total=10**6))
    assert budget.spent == 10**6


# ---------------------------------------------------------------------------
# prompt cache
# ---------------------------------------------------------------------------


def test_prompt_cache_key_determinism():
    req_a = LLMRequest(provider="claude", model="m", prompt="same", temperature=0.2)
    req_b = LLMRequest(
        provider="claude",
        model="m",
        prompt="same",
        temperature=0.2,
        metadata={"task_id": "ignored-in-key"},
    )
    # metadata must not affect the key
    assert compute_cache_key(req_a) == compute_cache_key(req_b)
    # a different param must change the key
    req_c = LLMRequest(provider="claude", model="m", prompt="same", temperature=0.9)
    assert compute_cache_key(req_a) != compute_cache_key(req_c)


def test_prompt_cache_hit_miss():
    cache = PromptCache()
    req = LLMRequest(provider="ollama", model="gemma", prompt="x")
    first = cache.lookup(req)
    assert first.hit is False
    second = cache.lookup(req)
    assert second.hit is True
    assert first.cache_key == second.cache_key
    assert cache.misses == 1
    assert cache.hits == 1
    # probe-only lookup does not register a new key
    probe_req = LLMRequest(provider="ollama", model="gemma", prompt="probe")
    cache.lookup(probe_req, remember=False)
    assert cache.lookup(probe_req).hit is False


# ---------------------------------------------------------------------------
# gateway
# ---------------------------------------------------------------------------


def test_gateway_generate_via_echo_provider():
    gateway = LLMGateway()
    gateway.register_echo("echo")
    req = LLMRequest(provider="echo", model="m", prompt="ping pong")
    resp = gateway.generate(req)
    assert "ping pong" in resp.text
    assert resp.usage.total > 0


def test_gateway_threads_budget_and_cache():
    budget = TokenBudget(total=1000)
    cache = PromptCache()
    gateway = LLMGateway(budget=budget, cache=cache)
    gateway.register_provider("echo", build_echo_provider())
    req = LLMRequest(provider="echo", model="m", prompt="a b c")
    gateway.generate(req)
    assert budget.spent > 0
    assert cache.misses == 1
    # identical request -> cache hit recorded
    gateway.generate(req)
    assert cache.hits == 1
    assert gateway.last_cache_lookup().hit is True


def test_gateway_unregistered_provider_raises():
    gateway = LLMGateway()
    with pytest.raises(ProviderNotRegistered):
        gateway.generate(LLMRequest(provider="nope", model="m", prompt="x"))


def test_stub_provider_refuses_to_fabricate():
    provider = build_claude_provider()
    with pytest.raises(ProviderNotImplemented):
        provider(LLMRequest(provider="claude", model="m", prompt="x"))
