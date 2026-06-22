# Forgekit provider policy — 계약 / slot 정책 / main-provider 기본값 / usage

> Forgekit 는 **provider-agnostic** 한 설치형 제품이다. Claude / Codex / Gemini /
> Ollama 그리고 사내(enterprise) endpoint 까지 모두 *하나의 고정 최소 계약*
> (`ProviderSpec`) 으로 기술되고, policy 가 그 계약 위에서 slot 배치 / 기본값 /
> usage 를 결정한다. **순수 데이터 + 검증만** — live submit 은 본 문서 범위 밖.
>
> 코드 SSoT:
> - 계약/built-in/registry: `apps/forgekit-console/src/forgekit_console/providers/`
> - slot/main-profile/usage 정책: `apps/forgekit-console/src/forgekit_console/policy/`
> - 짝 문서: capability 배치 SSoT 는 [`provider-capability-matrix.md`](provider-capability-matrix.md).

## 1. Provider 계약 (`ProviderSpec`)

모든 provider 가 conform 하는 고정 최소 계약. `providers/contract.py`.

| 필드 | 의미 | 허용값 |
| --- | --- | --- |
| `id` | provider 식별자 | non-empty |
| `label` | 사람용 이름 | non-empty |
| `kind` | 추론이 *어디서* 도는가 | `cloud_cli` / `cloud_api` / `local` / `enterprise` |
| `auth_kind` | 인증 방식 (kind 와 직교) | `oauth` / `api_key` / `none` / `endpoint` |
| `usage_mode` | 과금/가용 posture | `subscription` / `api` / `local` / `enterprise` |
| `submit_compat` | wire 모양 (기록만, live 미구현) | `cli` / `openai_compatible` / `custom_http` / `native` |
| `health_contract` | doctor 가 어떻게 검사하나 | `cli_present` / `api_key_set` / `endpoint_reachable` |
| `capability_flags` | vendor-neutral capability 힌트 | `chat`/`execution`/`research`/`synthesis`/`long_context`/`tool_use`/`cheap`/`safety`/`classification`/`local` |
| `endpoint` | local/enterprise 의 base URL | local/enterprise 는 필수 |
| `enterprise` | 사내 provider seam marker | `kind=enterprise` 와 함께 |

`validate_provider_spec(spec) -> tuple[errors]` 가 enum 멤버십 + cross-field 일관성을
검사한다: local/enterprise 는 endpoint 필수, `endpoint_reachable` 는 endpoint 필요,
`auth_kind=none` 은 local 에서만, `auth_kind=endpoint` 는 enterprise 에서만, usage_mode 는
kind 와 coherent 해야 한다.

### Built-in 4종 (`providers/builtins.py`)

capability 매트릭스를 각 provider 의 강점 평면에 투영:

| id | kind | auth | usage | capability lean |
| --- | --- | --- | --- | --- |
| `claude` | cloud_cli | oauth | subscription | synthesis / safety / tool_use / long_context |
| `codex` | cloud_cli | api_key | api | execution / tool_use |
| `gemini` | cloud_api | api_key | api | research / long_context / cheap |
| `ollama` | local | none | local | cheap / local / classification |

## 2. Policy mode × slot 해석 (`policy/provider_policy.py`)

slot = 에이전트가 하는 거친 작업 종류:
`default_chat` / `execution` / `research` / `synthesis` / `fallback`.

`resolve_slots(main_provider, mode, *, overrides={}, available=()) -> {slot: provider_id}`
는 순수·결정형이다 (같은 입력 → 같은 매핑, 감사 가능).

| mode | 동작 |
| --- | --- |
| `strict-single` | **모든 slot 을 main provider 로** 채운다. overrides 무시. 가장 단순, 한 청구서. |
| `hybrid` | main 이 기본, 운영자가 **명시적으로 지정한** slot 만 다른 provider 로 pin. auto-magic 없음. |
| `optimized` | hybrid + 어떤 slot 의 capability 를 다른 *available* provider 가 더 잘하면 **auto-pick**. override 가 항상 우선, main 이 결정형 fallback. |

slot → capability 매핑(optimized): execution→`execution`, research→`research`
(보조 `long_context`), synthesis→`synthesis`, fallback→`cheap`. `default_chat` 은
단일 "더 나은" capability 가 없어 main 에 머문다.

**unavailable override/auto-pick 은 거부되고 main 으로 fallback** 한다 — 결정형이며
없는 선택지에 대해 silent 하지 않는다(결과가 단순히 main id 를 담는다).

예 (`main=claude, optimized, available=codex,gemini`):
`execution→codex`, `research→gemini`, `synthesis→claude`, `fallback→gemini`,
`default_chat→claude`.

### 2.1 mode→slot: chat vs 비-chat 분리 (`policy/routing.py`)

제출은 두 종류다 — operator **CHAT** turn 과 자율 **NON-CHAT** work item. routing 은 이 둘을
분리한다(`WORK_CHAT` / `WORK_NONCHAT`):

- **chat** → 항상 `default_chat` slot. mode 가 chat 을 work slot 으로 몰래 끌고 가지 않는다
  (이전엔 delivery 모드의 chat 이 execution slot 으로 가는 conflation 이 있었음). 이는 live submit
  경로(`chat/service` 가 `default_chat` 사용)와 정확히 일치한다.
- **nonchat work** → `mode_work_slot(mode)`(research→research, delivery→execution, …). 자율 작업만
  mode 가 slot 을 결정한다.

`slot_for(mode, kind)` / `resolve_submit(cfg, mode, kind=WORK_CHAT)` 가 진입점. `mode_submit_slot`
은 back-compat(=`mode_work_slot`). 회귀 `tests/forgekit/test_routing.py`.

## 3. Main-provider 기본값 (`policy/main_profile.py`)

setup 의 단 하나의 결정 — "어느 provider 가 네 것인가" — 에서 기본값을 파생한다.
`profile_for(main_provider_id) -> MainProviderProfile`:
`default_policy_mode` / `agent_lean` / `default_usage_mode` / `warnings`.

| main | default mode | agent lean | default usage | warning |
| --- | --- | --- | --- | --- |
| claude | hybrid | synthesis-heavy | subscription | — |
| codex | hybrid | execution-heavy | api | — |
| gemini | hybrid | research-heavy | api | — |
| ollama | **strict-single** | local-first | local | **단일 로컬 모델 → capability 제한 경고** |

custom/enterprise main 은 capability flags 에서 lean 을 파생(local→local-first,
execution→execution-heavy, …), 미지의 provider 는 보수적으로 strict-single 로 시작.

## 4. Adaptive usage / billing / reserve (`policy/usage_policy.py`)

forgekit 는 청구하지 않지만 budget 이 터지기 전에 throttle/defer 할 수 있어야 한다.
구조 우선(billing 정확도 아님).

**usage modes**: `adaptive`(reserve 임계까지 자유롭게, 이후 throttle — 기본) /
`strict`(budget 에서 hard stop, reserve 없음) / `subscription_aware`(구독은 사실상
flat, reserve 만 rate-limit/fair-use cliff 방어) / `local_first`(local/무비용 우선,
필요한 slot 만 paid 로 spill).

**billing modes**: `subscription` / `api` / `local` / `enterprise` — provider 의
usage_mode 와 대응. `default_usage_policy(main_provider, billing_mode)` 가 매핑:
subscription→subscription_aware, api→adaptive, local→local_first, enterprise→adaptive.

**reserve** = budget 에서 held-back 한 fraction(0.0–1.0). 남은 budget 이 reserve 로
들어오면 spend 대신 throttle — task 중간 hard-fail 을 막는 안전 margin.
기본 reserve: adaptive 0.15 / strict 0.0 / subscription_aware 0.10 / local_first 0.05.

`should_throttle(policy, spent, budget)`: strict 는 `spent >= budget`, 그 외는
`spent >= reserve_floor(budget)`. budget<=0(unbounded/무비용)은 strict 가 아니면
throttle 안 함.

### 4.1 per-provider 일일 budget (`usage/provider_budget.py`)

global daily budget(`daily_token_budget`) 위에 **provider 별** 일일 token 한도를 둔다 —
한 brain 이 전체 예산을 태우지 못하게 하고, 유료 provider 를 ring-fence 한다. config:

```jsonc
"budget_policy": { "provider_daily_limits": { "gemini": 50000, "ollama": 0 } }
```

`0`/absent = **unbounded**(정직 — 한도를 임의로 만들지 않음). spend 는 ledger 의 **성공·non-throttle**
제출만 합산(held/throttle 는 태운 게 없음). 강제는 **routing 의 `available` seam**으로: 한도 초과
provider 는 *unavailable* → `resolve_routing` 이 다음 후보로 **정직하게 fallback**, live submit 체인
(`chat/service`)도 동일하게 over-budget head 를 skip(faked send 없음). chain 전부 초과면
`budget_throttled`. 영속 writer 는 `provider_ops.set_provider_budget`(canonical config, reload 유지),
표면은 `/provider`(`provider_surface` 가 설정 한도 표기). provider-neutral(특정 vendor 분기 없음).
회귀 `tests/forgekit/test_provider_budget.py`, evidence `examples/provider-budget/`.

## 5. Enterprise / internal seam (`providers/registry.py`)

provider 가 forgekit 에 들어오는 두 경로:

1. **built-in id** — `claude`/`codex`/`gemini`/`ollama` 를 바로 해석.
2. **generic config dict** — custom/enterprise/internal provider. 같은 계약으로
   load 되므로 사내 gateway 를 가리킬 수 있다. live submit 은 미구현 —
   registry 가 *shape 를 검증*해 setup/doctor 가 추론할 수 있게 하고, 실제 transport
   결선은 후속 work tree.

config shape 3종:

| shape | 기본 kind | 기본 auth | 기본 submit | health |
| --- | --- | --- | --- | --- |
| `openai-compatible` | cloud_api | api_key | openai_compatible | api_key_set |
| `custom-http` | enterprise | endpoint | custom_http | endpoint_reachable |
| `internal-enterprise` | enterprise | endpoint | custom_http | endpoint_reachable |

`validate_config(config) -> tuple[errors]` 와 `build_provider(config) -> ProviderSpec`
(invalid → `ProviderConfigError`). endpoint-bearing shape 는 endpoint 필수, 미지의
shape/빈 id/빈 label 은 거부.

`no_provider_configured(config)` — **setup-incomplete signal**: config 가 없거나
`main_provider`/`id`/`providers` 가 전혀 없으면 True(provider 가 아직 안 골라짐).

## 6. CLI 읽기 표면

```
forgekit provider list                       # built-in 계약 출력
forgekit provider slots <main> [--mode m] [--available a,b]   # slot 해석 출력
```

`cli/provider_cmd.py`. read-only, live submit 없음.

## 7. 테스트
`tests/forgekit/test_providers.py`(계약 검증 + built-in + enterprise seam),
`tests/forgekit/test_policy.py`(slot 해석 + main-profile + usage). 실행:
`python3 -m unittest discover -s tests/forgekit -p 'test_*.py'`.
