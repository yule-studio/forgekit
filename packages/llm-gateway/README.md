# yule-llm-gateway

A **minimal central interface** for LLM provider calls (Claude / Gemini / Ollama /
Codex) plus token-budget and prompt-cache *metadata*. This package is **additive
and conservative**: it does not move or rewire any existing runner. The intent is
the seam, not a finished abstraction —

> 처음부터 완벽한 추상화를 만들지 말고 최소 인터페이스만 만드세요.

## Responsibility

| Module | Purpose |
| --- | --- |
| `models.py` | `LLMRequest`, `LLMResponse`, `TokenUsage`, `Message` — small, trackable, JSON-friendly wire shapes. |
| `token_budget.py` | `TokenBudget` — total / spent / remaining, `record` (observe) and `charge` (enforce, raises `BudgetExceededError`). |
| `prompt_cache.py` | `PromptCache` — deterministic `cache_key` from (provider, model, prompt, params) + hit/miss metadata. **Not** a real response cache. |
| `client.py` | `LLMGateway` — the single pluggable `generate(LLMRequest) -> LLMResponse` entry point agents COULD adopt. Routes to registered provider callables. |
| `providers/` | Thin adapters / placeholders. `build_*_provider()` factories; real ones raise `ProviderNotImplemented`, plus an echo provider for tests/dry-run. |

## Minimal-interface intent

The gateway does **not** call any real provider yet. It dispatches to a
*registered* provider callable. A built-in echo provider lets tests and the
dry-run path exercise the seam without contacting a backend. The real runners
keep working unchanged; migration means *registering* them here, not rewriting
them.

## Dependency rule

Standard library only. `yule_llm_gateway` MUST NOT import `yule_engineering`
(the app) or any `apps/*` code — the arrow always points the other way
(`app -> gateway`). The provider placeholders deliberately do **not** import the
real runners, to avoid blast radius and import cycles.

## TODO — provider call sites to migrate later

These existing call sites are the ones a later milestone should wrap behind
`LLMGateway.register_provider(...)`. They are referenced (in docstrings) but
**not imported** by this package:

- [ ] `apps/engineering-agent/src/yule_engineering/agents/runners/claude_code.py` — `ClaudeCodeRunner` (local `claude` CLI).
- [ ] `apps/engineering-agent/src/yule_engineering/agents/runners/gemini.py` — `GeminiRunner` (`gemini` CLI, long-context).
- [ ] `apps/engineering-agent/src/yule_engineering/agents/runners/codex.py` — `CodexRunner` (`codex` CLI, advise/review/patch).
- [ ] `apps/engineering-agent/src/yule_engineering/agents/runners/bootstrap.py` — env-driven runner wiring (`build_role_runner_candidates`).
- [ ] `apps/engineering-agent/src/yule_engineering/planning/ollama.py` — `generate_ollama_text` / `generate_human_briefing` (HTTP `/api/generate`).
- [ ] `apps/engineering-agent/src/yule_engineering/planning/ollama_config.py` — `OllamaPlanningConfig` / `OllamaConversationConfig` (env config).

## Usage

```python
from yule_llm_gateway import LLMGateway, LLMRequest, TokenBudget, PromptCache

gateway = LLMGateway(budget=TokenBudget(total=10_000), cache=PromptCache())
gateway.register_echo("claude")  # or register_provider("claude", real_callable)

resp = gateway.generate(
    LLMRequest(provider="claude", model="claude-x", prompt="hello", max_tokens=256)
)
print(resp.text, resp.usage.total)
```
