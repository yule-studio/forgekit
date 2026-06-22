# PM / Tech-Lead Lane — 설계 결정 레인 (SSoT)

> 본 문서는 **PM → gateway → tech-lead → engineer** 로 흐르는 "설계 결정
> 레인" 의 단일 설계 SSoT 다. 코드 SSoT 는
> [`packages/forgekit-runtime/src/forgekit_runtime/decision_lane/`](../packages/forgekit-runtime/src/forgekit_runtime/decision_lane/)
> (`schemas.py` / `validators.py` / `lane.py` / `enforcement.py`) +
> 실행 루프 연결 [`autopilot/orchestrator.py`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/orchestrator.py)
> (`execution_authorizer`). 회귀는
> [`tests/forgekit/test_pm_techlead_lane.py`](../tests/forgekit/test_pm_techlead_lane.py)
> + [`test_pm_techlead_enforcement.py`](../tests/forgekit/test_pm_techlead_enforcement.py)
> + [`test_exec_lane_enforcement.py`](../tests/forgekit/test_exec_lane_enforcement.py),
> evidence 는 [`apps/forgekit-console/examples/pm-techlead-lane/`](../apps/forgekit-console/examples/pm-techlead-lane/).
>
> 식별자(역할)는 [`forgekit_config.identity.registry`](../packages/forgekit-config/src/forgekit_config/identity/registry.py)
> 한 곳에서만 정규화한다(`be`→`backend-engineer`). 승인 ladder(L0~L4)는
> [`forgekit_runtime.autopilot.approval`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/approval.py)
> 를 재사용한다 — 중복 정의 금지.

## 0. 한 줄 요약

- **PM** = 문제·사용자가치·수용 기준(acceptance)·성공 지표를 정의. 기술
  결정/실행 권한 없음.
- **gateway** = 라우팅만. PM brief + meeting 이 **실재** 하는지만 확인하고
  tech-lead 로 전달. 기술 내용을 결정하지 않는다.
- **tech-lead** = **유일한 기술 승인자**. design system / coding convention /
  stack 결정 / tradeoff / approval 을 하나의 `TechLeadDecision` 으로 고정.
- **engineer** = **단일 executor**. 서명된 결정 + 유효한 handoff 위에서만
  착수.
- **fake meeting / fake signoff 는 실행에 도달하지 못한다** — gate 는
  `run_lane` / `can_engineer_start`.

## 1. 왜 필요한가

기존 autopilot 체인([`autopilot/chain.py`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/chain.py))
은 작은 repo *finding*(docs/lint/test)을 PM→gateway→tech-lead 로 흘려 SAFE
클래스를 사용자 승인 없이 실행한다. 하지만 그것은 **finding** 레인이지
**설계 결정** 레인이 아니다. 설계 제안(예: "알림 전달 스택을 무엇으로?")
은 다음을 요구한다.

1. PM 이 **사용자 가치 + 수용 기준** 으로 문제를 프레이밍 (단순 기술 에러
   아님).
2. 후보 스택을 **2개 이상 비교** 하고 tradeoff 와 함께 하나를 권고.
3. 그 비교/결정이 **실재하는 회의** 에서 합의/escalate 됨 — 한 명이 도장만
   찍는 "다 같이 잘하자" fake 합의 금지.
4. tech-lead 가 design system / coding convention / stack / tradeoff /
   approval 을 한 결정으로 고정 서명.
5. **단일 engineer** 에게 scope/forbidden_scope/test 전략과 함께 인계.

본 레인은 이 5가지를 타입 + validator + handoff 규칙으로 강제한다.
[`engineering-role-council-runtime.md`](engineering-role-council-runtime.md)
의 council(3-seat) 모델과 hard rail(single executor / technical vs operator
approval 분리)을 약화하지 않고 **설계 결정 표면에 구체화**한다.

## 2. Artifact 스키마 (코드 SSoT: `schemas.py`)

| artifact | 핵심 필드 | 무엇을 보장 |
|---|---|---|
| `PMBrief` | topic / problem / user_value / **acceptance_criteria** / **success_metrics** / out_of_scope | 문제와 완료 기준을 PM 이 고정 |
| `StackOption` | name / pros / **cons** / risk / fit | 한 후보의 정직한 장단점 |
| `StackComparison` | options(≥2) / **recommended** / rationale / **tradeoffs** | 비교 + 권고 + 포기한 것 |
| `ParticipantPosition` | role / **stance** / position / concerns | 회의의 *실재* 단위 |
| `MeetingRecord` | meeting_id / agenda / participants / decisions / escalated | 기록된 설계 회의 |
| `TechLeadDecision` | meeting_ref / **design_system** / **coding_convention** / stack_decision / tradeoffs / approval_level / signoff_by / status | 기술 서명(5개 필수 필드 고정) |
| `EngineerHandoff` | decision_ref / **executor_role**(단일) / scope / forbidden_scope / test_strategy / rollback_plan | 단일 executor 작업 지시 |

모든 artifact 는 frozen dataclass + `to_dict` (직렬화/evidence 가능).

## 3. 설계 결정에 반드시 포함하는 것 (`TechLeadDecision` 필수)

tech-lead 서명(`signed_off`/`conditional`)은 아래 5가지가 모두 채워질
때만 성립한다. 하나라도 비면 validator 가 막고 status 는 `escalated` 로
강등된다 (= fake signoff 금지).

1. **design system** — 어떤 디자인 시스템/토큰을 따르는가 (예: forgekit
   tokens v2, shadcn 기반 등). 비면 거부.
2. **coding convention** — 린트/포맷/네이밍/commit 규칙 (예: ruff+black,
   한글 gitmoji commit). 비면 거부.
3. **stack decision** — `StackComparison` (후보 ≥2, 각 후보 pros+cons,
   recommended ∈ options, rationale). 한쪽만 보는 비교는 거부.
4. **tradeoff** — 권고안이 포기하는 것 ≥1. "공짜 선택" 거부.
5. **approval** — `risk_class` + `approval_level`(L0~L4) + `signoff_by`
   (반드시 canonical `tech-lead`).

## 4. Handoff 규칙 (gateway → tech-lead → engineer)

```text
PMBrief + MeetingRecord
   │  route_to_tech_lead()         ← gateway: brief/meeting 실재 검증만, 기술결정 X
   ▼
GatewayRouting(forwarded=True)
   │  tech_lead_decide()           ← tech-lead: 분류 + 서명 (validator 통과시에만 signed)
   ▼
TechLeadDecision(status ∈ signed_off/conditional/blocked/escalated)
   │  handoff_to_engineer()        ← 단일 executor 작업지시 (operator_required 세팅)
   ▼
EngineerHandoff
   │  can_engineer_start()         ← HARD GATE
   ▼
engineer 착수 (단일 executor)
```

**Hard gate (`can_engineer_start`)** — 다음이 모두 참일 때만 engineer 착수:

- `decision.status ∈ {signed_off, conditional}` (서명 안 됨 → 불가)
- `validate_tech_lead_decision(decision) == ()` (fake signoff → 불가)
- `validate_handoff(handoff, decision) == ()` (executor 가 단일 엔지니어, scope/test 존재)

이는 autopilot 의 `can_specialist_execute`("no internal signoff, no
execution")를 설계 결정 표면으로 옮긴 것이다. `decision=None` 또는
`handoff=None` 이면 항상 False.

### 4.1 approval ladder 와 operator 분리

| risk_class | approval_level | engineer handoff | operator |
|---|---|---|---|
| safe | L2_internal_approve | 가능, `operator_required=False` | 불요 (내부 승인으로 충분) |
| risky | L3_user_approve | `signed_off` 이지만 `operator_required=True` | **필요** (실행 전 운영자 승인) |
| blocked | L4_restricted | `status=blocked` — handoff 없음 | operator + runbook 전용 |

tech-lead 의 **technical signoff** 가 operator approval 을 대신하지 않는다
(L3/L4 는 그대로 `#승인-대기`). 경계 규칙 SSoT 는
[`engineering-role-council-runtime.md`](engineering-role-council-runtime.md) §5.

## 5. fake meeting / fake signoff 금지 (validator)

코드 SSoT: `validators.py`. 각 validator 는 위반 문자열 tuple 을 반환하고
`()` 면 실재로 인정.

- **fake meeting** (`validate_meeting`): 참석자 <2, 서로 다른 역할 <2, 빈
  발언(position), agenda 없음, **반대/조건부/우려가 하나도 없는 rubber-
  stamp 합의**, 결정도 escalation 도 없는 미결 회의 → 거부.
- **fake signoff** (`validate_tech_lead_decision`): meeting_ref 없음,
  rationale 없음, design_system/coding_convention 없음, stack 비교 부실,
  tradeoff 없음, `signoff_by` 가 tech-lead 가 아님 → 거부 (status 강등).
- **fake handoff** (`validate_handoff`): 서명 안 된 결정에서 인계, executor
  가 gateway/tech-lead/PM(=router/decider), scope/test 전략 없음 → 거부.

## 6. Stack 비교/권고 구조

설계 제안 시 "어떤 stack 이 좋은가" 를 다루는 1급 구조가 `StackComparison`.

- `options`: 후보 ≥2, 각각 `pros` **와** `cons` 를 모두 가짐 (한쪽만 보면
  거부 — 정직한 비교 강제).
- `fit`: 0~100 주관 적합도(비교 보조용).
- `recommended`: 반드시 `options` 중 하나, `rationale` 와 `tradeoffs` 동반.

이 구조는 tech-lead 결정에 그대로 박혀(`TechLeadDecision.stack_decision`)
설계 문서/evidence 로 직렬화된다.

## 7. Runtime 실행 강제 (execution binding) — `enforcement.py`

레인은 **결정** 하고, 본 절은 그 결정이 **실행 시점에 무는** 부분이다. 모든
실행 시도는 `authorize_execution` 한 곳을 통과해야 하며, 이 함수는 *서명된
action* 이 아니라 *실제 action* 에 대해 승인 체인 전체를 다시 검사한다.

### 7.1 단일 chokepoint

`assert_executable(decision, handoff, request, *, routing, operator_approval)` —
승인되면 `ExecutionVerdict`, 아니면 `ExecutionBlocked` raise. 실제 mutation
직전에 이 게이트를 통과하지 않는 실행 경로는 없다.

검사 항목(모두 충족해야 `allowed`):

1. **gateway 승인** — `GatewayRouting.forwarded` (미경유 실행 차단).
2. **tech-lead 서명** — 실재하고 validator 통과한 `TechLeadDecision`
   (`signed_off`/`conditional`). fake signoff → 차단.
3. **engineer handoff** — 단일 executor, validator 통과.
4. **실행 시점 분류** — `classify_action(request)` 가 action 을 **safe /
   risky / destructive** 로 재계산.
5. **operator 승인** — risky 면 `OperatorApproval`(승인자 + decision 일치)
   필수. destructive 는 자동 실행 자체 금지.

### 7.2 destructive / risky / safe 를 실행 경로에서 실제 적용

분류는 **서명 시점이 아니라 실행 시점에** 다시 계산한다. 그래서 "safe" 로
서명해 놓고 `deploy` 를 끼워 넣을 수 없다 — 실제 action 의 등급이 서명 등급을
초과하면 **scope-creep** 으로 차단되고 재서명을 요구한다.

| action_class | 분류 근거 | 실행 조건 | commit |
|---|---|---|---|
| **safe** (L2) | kind ∈ `SAFE_CLASS_ALLOWLIST` + 안전 wording | 내부 서명만으로 실행 | trailer 필수 |
| **risky** (L3) | risky wording / unknown kind / risk_flag | **operator 승인** 필요 | trailer 필수 |
| **destructive** (L4) | kind ∈ `AUTO_FORBIDDEN`(deploy/secret/infra…) / 제한 wording | **자동 실행 금지** — operator+runbook | 경로 차단 |

unknown kind 는 자동 safe 가 아니다(safe-by-rejection → risky 로 승격). 분류
어휘/allowlist 의 SSoT 는 [`autopilot/approval.py`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/approval.py)
+ [`autopilot/execution.py`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/execution.py).

### 7.3 실제 실행 경로와의 연결 (autopilot bridge)

`bridge_to_autopilot(decision)` 가 레인 서명을 autopilot 실행 게이트의
`TechLeadDecision` 으로 변환한다. 그래서 실제 write 는 기존
[`autopilot.validate_execution`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/execution.py)
+ `BoundedMutator`(verified, hard-capped) 경로가 수행하되, **레인 서명이
있어야만** `can_execute=True`(safe/L2 한정)가 된다. risky/blocked 는
`can_execute=False` 로 매핑돼 자동 실행 불가.

#### 7.3.1 autopilot 실행 루프 in-loop 강제 (orchestrator)

레인을 **실제 돌아가는 실행 루프** 에 묶는 지점은
[`AutopilotOrchestrator.execution_authorizer`](../packages/forgekit-runtime/src/forgekit_runtime/autopilot/orchestrator.py)
다. `decision_lane.make_runtime_authorizer(...)` 가 만든 콜러블을 주입하면,
orchestrator 는 mutator 직전에 `(finding, decision, executor, risk_class)` 로
호출하고 **non-allowed verdict 면 그 항목을 실행하지 않는다**(in-loop 거부).
주입이 없으면 legacy 동작(체인 게이트만) — 하위 호환.

연결은 `authorize_runtime_execution` 이 담당한다. 이것은 design-meeting 레인이
아니라 autopilot **finding 체인** 의 `TechLeadDecision`(= 실제 PM→gateway→
tech-lead 서명)을 그대로 소비하면서 §7.1~7.2 와 동일한 규칙을 적용한다.

- **defense-in-depth 재분류:** 체인의 게이트는 텍스트만 보지만, lane 은
  실행 시점에 `kind`(예: `deploy` ∈ `AUTO_FORBIDDEN`)까지 본다. 그래서 텍스트가
  안전해 보여 체인이 `can_execute=True` 로 통과시킨 forbidden-kind 작업을 lane 이
  **destructive 로 재분류해 in-loop 차단** 한다(회귀:
  `test_exec_lane_enforcement`).
- **executor 검증:** 실행 슬롯은 engineering-dept 역할이어야 하고 gateway/PM 은
  거부(단, finding 경로에선 체인이 docs→tech-lead 로 라우팅하므로 tech-lead 는
  허용 — design 레인의 `NON_EXECUTOR_ROLES` 와 구분).
- **approval metadata 바인딩:** 승인되어 실행된 항목은 `executed[].approval` 로
  decision/level/signoff(+operator) 메타데이터를 달고 기록된다 → §7.4 의 commit
  trailer 와 동일 값.

### 7.4 commit trailer / agent identity / approval metadata 바인딩

승인된 작업의 commit 은 승인 메타데이터를 **반드시** 달고 나가야 한다.

- `execution_commit_trailers(verdict)` → registry 기반
  [`attribution.commit_trailers`](../packages/forgekit-config/src/forgekit_config/identity/attribution.py)
  로 `Forgekit-Agent` / `Forgekit-Role` / `Forgekit-Mode`(action_class) /
  `Forgekit-Handoff-From/To` / `Forgekit-Approval`(decision id + level +
  signoff + operator) 트레일러 생성. **차단된 verdict 에는 트레일러를 만들지
  않는다**(fake approval 금지).
- `validate_execution_trailers(message, verdict)` → commit 메시지가 그 verdict
  의 실제 executor/approval 트레일러를 담고 있는지 검증. 누락/불일치면 거부
  → 작업 경로가 승인에 묶인다.

### 7.5 차단되는(절대 실행 금지) 경로 — 명시

- gateway 라우팅 없이 도달한 실행 → 차단.
- tech-lead 서명 없음 / fake signoff → 차단.
- handoff 무효(비-엔지니어 executor 등) → 차단.
- risky 인데 operator 승인 없음/대상 불일치 → 차단.
- destructive(deploy/secret/infra) → 자동 실행 금지(operator+runbook).
- 실제 action 등급 > 서명 등급(scope creep) → 차단(재서명).
- 승인 메타데이터 없는 commit → `validate_execution_trailers` 거부.

## 8. 한계 / 비목표

- 본 레인은 **결정 흐름과 그 실재성** 만 강제한다. 회의 *내용* 의 옳고
  그름(어떤 스택이 정답인지)을 판정하지 않는다 — 그것은 council 토의와
  사람 판단의 몫.
- 실제 파일 mutation 자체는 본 모듈이 하지 않는다 — `enforcement.py` 는
  **승인 게이트**(authorize/assert + trailer 검증)이고, 실 write 는 §7.3
  의 autopilot `BoundedMutator`(verified, hard-capped) 경로가 수행한다.
  본 레인은 그 경로가 **레인 서명 없이는 실행되지 못하도록** 묶는다.
- self-improvement 자동 promote 금지 (기존 hard rail 유지).

## 9. 동기화

본 문서를 추가/변경하면 다음만 갱신한다 (중복 회피).

- [`CLAUDE.md`](../CLAUDE.md) "Runtime governance hard rails" — cross-link 1줄
- [`AGENTS.md`](../AGENTS.md) §2 — 작업 맥락(설계 결정 레인) → 본 문서 매핑
- [`engineering-role-council-runtime.md`](engineering-role-council-runtime.md)
  §5 — approval 경계는 거기 SSoT, 본 문서는 표면 구체화로 cross-link
