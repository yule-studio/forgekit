# Rule-first LLM minimization — 무엇을 아예 LLM으로 보내지 않는가 (SSoT)

> 토큰 효율의 다음 레버는 "덜 보내기"가 아니라 **"아예 LLM을 부르지 않기"** 다.
> 입력 단위로 *규칙으로 끝낼 일* 과 *LLM이 필요한 일* 을 선언적으로 고정하고, 그 결정이
> routing / receipt / insights 에 반영된다. 코드 SSoT 는
> [`agents/harness/llm_minimization.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/llm_minimization.py).

## 1. resolution_mode (입력 단위 판정)

| mode | 의미 | 기본 backend 경로 |
| --- | --- | --- |
| `rule_first` | 규칙/결정형으로 끝낸다 — live LLM 건너뜀 | deterministic/rule path 먼저 |
| `llm_optional` | LLM 도움되나 cheap/local 우선 | Ollama/Gemini 먼저 |
| `llm_required` | 강한 LLM 경로 유지 | Codex/Gemini/Claude |

각 결정은 `capability_class` / `resolution_mode` / `llm_allowed` / `why` 를 가진다.

## 2. capability_class → resolution_mode 매핑

| capability_class | mode | 근거 |
| --- | --- | --- |
| classification · enforcement · security_gate · verification · memory | **rule_first** | 분류/grant/policy/receipt formatting/cache-hit 은 규칙으로 결정 가능 |
| summarization · compaction | **llm_optional** | LLM 도움되나 cheap/local 우선 |
| research · execution · delivery · exploration | **llm_required** | 강한 추론/도구 조작/대용량 |

미지정/미지 capability → **`llm_required`(default)** — 안전하게 기존 동작 유지(절대 silent bypass 안 함).

## 3. explicit override (운영자 우선)

`RoleRunnerInput.metadata` 에 `resolution_mode` / `llm_allowed` 가 있으면 capability 매핑보다 우선.
예: classification 이라도 `resolution_mode=llm_required` 면 LLM 경로 강제.

## 4. routing 반영 (Phase B)

`agents/runners/capability_routing.order_providers_for_resolution`:
- `rule_first` → `deterministic` 를 **맨 앞**으로 → rule path 가 이기고 live provider skip.
- `llm_optional`/`llm_required` → capability 선호 순서(cheap-first/strong-first), `deterministic` 마지막.
- **deterministic fallback 은 항상 마지막 안전망** 으로 남는다.

게이트웨이 결선: `bootstrap` 이 `YULE_LLM_MINIMIZATION_ENABLED`(기본 off) 면 resolution router 를
capability router 위에 얹는다. 둘 다 off 면 기존 우선순위 그대로.

## 5. receipt 반영 (Phase D)

`ExecutionReceipt.optimization` 가 판단 근거를 남긴다:
`capability_class / resolution_mode / llm_allowed / llm_used / selected_provider /
bypassed_live_llm / bypass_reason / routing_reason`. receipt 가 감사용을 넘어 **최적화 근거** 가 된다.

## 6. insights 반영 (Phase E)

`yule harness insights --receipts <json>` / `--session <id>` 가 execution receipt 를 모아:
`rule_resolved_runs / llm_used_runs / llm_bypassed_runs / live_llm_avoided_rate /
resolution_mode 분포 / provider 사용`. "토큰 절감" 뿐 아니라 **"LLM 호출을 얼마나 줄였나"** 를 본다.

## 7. capability 추론 (Phase C, 보수적)

`standalone_runners._infer_capability_class` 는 명확한 경우만 capability_class 를 붙인다:
- `security-engineer` → `security_gate`
- `qa-engineer` + test 류 task → `verification`
- 그 외 → 미설정(현재 동작 유지). 과한 추론 금지.

## 8. 안전 원칙
- 모든 flag 기본 off — 기존 live path 무손상.
- deterministic fallback 은 항상 마지막 안전망.
- rule_first 도 explicit override 로 LLM 강제 가능.
- 미지 capability 는 `llm_required` — 절대 silent bypass 안 함.

## 9. 관련
- [`provider-capability-matrix.md`](provider-capability-matrix.md) · [`plugin-taxonomy.md`](plugin-taxonomy.md)
- 회귀: `tests/runners/test_llm_minimization.py` · `tests/agents/test_optimization_receipt.py` · `tests/agents/test_insights.py`
