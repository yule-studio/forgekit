# per-provider budget + mode→slot 분리 — evidence

`provider-budget-evidence.txt` 는 wave-2 multi-brain routing gap 마감을 **deterministic**
(fake transport·temp home·no net)으로 캡처한 것이다. SSoT: [`docs/forgekit-provider-policy.md`](../../../../docs/forgekit-provider-policy.md)
§4.1(per-provider budget) · §2.1(mode→slot 분리). 회귀: `tests/forgekit/test_provider_budget.py`,
`tests/forgekit/test_routing.py`.

증명:
1. **per-provider 한도 영속** — `set_provider_budget` → `persist_config` → reload 후에도
   `budget_policy.provider_daily_limits` 유지(재실행-후-유지).
2. **초과 → 정직 fallback** — gemini 가 일일 한도 초과면 routing/submit 이 gemini 를 건너뛰고
   다음 후보(ollama)로 **정직하게 fallback**(faked send 없음). 전 chain 초과면 `budget_throttled`.
3. **mode→slot chat/비-chat 분리** — operator CHAT 은 mode 와 무관하게 항상 `default_chat`,
   비-chat WORK 만 mode 의 work slot(research 등)로 라우팅.

재생성:

```
PYTHONPATH=<packages/*/src:apps/*/src> python3 \
  apps/forgekit-console/examples/provider-budget/_regen.py \
  > apps/forgekit-console/examples/provider-budget/provider-budget-evidence.txt
```
