# PM / Tech-Lead Lane — 설계 결정 레인 (SSoT)

> 본 문서는 **PM → gateway → tech-lead → engineer** 로 흐르는 "설계 결정
> 레인" 의 단일 설계 SSoT 다. 코드 SSoT 는
> [`packages/forgekit-runtime/src/forgekit_runtime/decision_lane/`](../packages/forgekit-runtime/src/forgekit_runtime/decision_lane/)
> (`schemas.py` / `validators.py` / `lane.py`), 회귀는
> [`tests/forgekit/test_pm_techlead_lane.py`](../tests/forgekit/test_pm_techlead_lane.py),
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

## 7. 한계 / 비목표

- 본 레인은 **결정 흐름과 그 실재성** 만 강제한다. 회의 *내용* 의 옳고
  그름(어떤 스택이 정답인지)을 판정하지 않는다 — 그것은 council 토의와
  사람 판단의 몫.
- 자동 실행 teeth 없음. handoff 는 작업 지시일 뿐, 실제 mutation 은
  autopilot mutator / coding job 경로가 별도 승인 게이트로 수행.
- self-improvement 자동 promote 금지 (기존 hard rail 유지).

## 8. 동기화

본 문서를 추가/변경하면 다음만 갱신한다 (중복 회피).

- [`CLAUDE.md`](../CLAUDE.md) "Runtime governance hard rails" — cross-link 1줄
- [`AGENTS.md`](../AGENTS.md) §2 — 작업 맥락(설계 결정 레인) → 본 문서 매핑
- [`engineering-role-council-runtime.md`](engineering-role-council-runtime.md)
  §5 — approval 경계는 거기 SSoT, 본 문서는 표면 구체화로 cross-link
