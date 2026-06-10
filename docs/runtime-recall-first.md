# F16 — Gateway Recall-First Decision Loop

> **Status**: F16 PR-1 (issue #128). 본 doc 은 변경 A (gateway 의사결정 순서 재정렬) 의 **단일 진실**. 코드 land 전에 의도와 7-action 표를 먼저 docs-only commit 으로 lock down 한다.

## 1. 현재 문제

`discord/engineering_channel_router.route_engineering_message` 는 사용자 메시지를 받으면 4 개 back-reference intent (continue / summarize / append / status) 만 runtime preflight 로 처리하고, **나머지 모든 자연어** 는 `conversation_fn(... auto_collect=True)` 로 떨어진다. 그 결과 `engineering_conversation._maybe_run_auto_collect` 가 즉시 research collector 를 호출하고, kickoff 직후 `_run_research_loop_hook` 가 항상 추가로 도는 구조다.

이는 단순 주제 / 의견 질문 / "기존 결정을 다시 확인" 같은 메시지에도 research 가 도는 낭비를 만든다. 사용자 의도: gateway 가 먼저 기존 지식 (Obsidian / RAG / CAG / session.extra / memory) 을 확인한 뒤, **부족하다고 판단된 경우에만** research 또는 tech-lead handoff 로 가야 한다.

## 2. 새 의사결정 순서

`apps/engineering-agent/src/yule_engineering/agents/runtime/loop.py` 에 이미 정의된 7-stage:

```
Observe → Understand → Recall → Research → Decide → Act → Record
```

이 loop 는 **존재하지만 gateway 경로에서 일부만 호출** 된다. F16 PR-1 의 본질은 "이 loop 의 Recall+Decide 단계를 gateway 의 전체 intent 에 대해 적용" 하는 것이지, 새 loop 를 만드는 것이 아니다.

### 2.1 Recall 단계 — Coverage 점수

`RuntimeRecallResult.coverage: RecallCoverage` 신규 필드. 다음 기준으로 high / medium / low + stale 결정:

| Coverage | 조건 |
| --- | --- |
| **high** | session matched + memory_hits ≥ 2 + 최근 24h |
| **medium** | session 또는 memory_hits ≥ 1 + 최근 7d |
| **low** | 둘 다 부족 또는 24h 이상 stale 우세 |

`stale` 은 별도 boolean — 가장 최근 source 가 7 일 이상이면 stale=True. high 라도 stale 이면 targeted research 로 강등.

### 2.2 Decide 단계 — 7-action 매트릭스

`runtime/decide.py` 의 신규 `decide_gateway(observation, intent, recall, plan, input_)` 가 다음 7 분기 중 하나를 반환:

| Action | When | What |
| --- | --- | --- |
| **GATEWAY_REPLY_ONLY** | general_chat / status / diagnostic + coverage=high + stale_low | gateway 가 recall hits 만으로 짧게 답. research 안 함. |
| **GATEWAY_ASK_CLARIFICATION** | intent=clarification_needed 또는 understand 가 결정 못함 | 사용자에게 추가 질문. |
| **GATEWAY_JOIN_EXISTING** | continue_existing_work + matched_session | 기존 session thread 에 메시지 합류, 새 work order X. |
| **GATEWAY_APPEND_CONTEXT** | append_context + matched_session | session.extra 에 새 컨텍스트 append, 즉답 X. |
| **GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH** | new_work_request + coverage=high + 의견/방향성 신호 ("어떻게 생각", "방향", "옵션", "추천") | tech-lead 에게 구조화 handoff. research run=False. |
| **GATEWAY_TARGETED_RESEARCH** | new_work_request + coverage=medium 또는 high+stale | `RuntimeResearchPlan(mode="targeted", max_provider_calls=2)`. |
| **GATEWAY_FULL_RESEARCH** | new_work_request + coverage=low 또는 explicit "조사해줘 / 리서치만" | 기존 `auto_collect=True` 와 동일 path. |

### 2.3 Legacy fallback

`route_context.prefer_legacy_auto_collect: bool = False` (신규 필드). True 일 때만 옛 `conversation_fn(auto_collect=True)` path 가 실행되어 회귀 검증용으로 남는다. 1 milestone 후 deprecate.

## 3. 안전 / Degrade

- memory / retrieval 호출이 raise 하면 `RecallCoverage(level="low", stale=True, sources=())` 로 안전 강등 — research 가 너무 자주 도는 함정에 빠지지 않게 stale 우선 표시.
- `decide_gateway` 가 분기 결정 못 하면 `GATEWAY_ASK_CLARIFICATION` 으로 fallback. 절대 "조용히 auto_collect" 안 함.
- Explicit research_only phrase ("조사해줘", "리서치만", "research only") 는 항상 `GATEWAY_FULL_RESEARCH` — coverage 검사 우회.

## 4. 관측

각 의사결정은 `agent_ops_log` 에 audit entry 추가:

```
{
  "stage": "decide_gateway",
  "intent": "new_work_request",
  "coverage": {"level": "high", "stale": false, "sources": ["session", "memory:obsidian", "memory:rag"]},
  "action": "GATEWAY_HANDOFF_TECH_LEAD_NO_RESEARCH",
  "explicit_research_phrase": false
}
```

이 entry 로 어느 action 분기가 가장 자주 호출되는지 분석 가능.

## 5. Acceptance Criteria

PR-1 머지 시점:

1. `runtime/recall.py` 의 `compute_recall_coverage` 가 신규 단위 test 모두 통과
2. `runtime/decide.py` 의 `decide_gateway` 가 7-action 매트릭스 test 모두 통과
3. `engineering_channel_router._run_runtime_preflight` 의 short-circuit intent 가 전체 intent 로 확장
4. `route_context.prefer_legacy_auto_collect=False` (기본) 시 자연어 메시지가 새 path 로 흐름
5. `prefer_legacy_auto_collect=True` 시 회귀 test 통과
6. status / diagnostic / continue_existing_work / summarize_previous_work 회귀 X

## 6. 후속 (PR-2 변경 B 와 연결)

PR-2 의 PR approval/merge loop 와 직접 의존 없음. 다만 새 `decide_gateway` 가 "PR merge 검토" 를 한 분기로 추가할 가능성은 후속 — 본 PR-1 scope 밖.

## 7. 참고

- `policies/runtime/agents/engineering-agent/recall-policy.md` — 기존 recall 정책 (본 doc 이 갱신 / 확장)
- `policies/runtime/agents/engineering-agent/mvp-scope.md` — MVP 우선순위
- 사용자 지시 (2026-05-13): "auto_collect 가 앞단에 너무 일찍 호출되는 구조를 고쳐라"
