# Engineering Role Council Runtime — Design

> 본 문서는 engineering-agent 를 "실제 직원처럼 계획하고, 조사하고, 설계
> 하고, 같은 전공끼리 토의하고, tech-lead 의 기술 승인과 gateway 의 운영
> 승인 surface 를 거쳐 실행하는" **부서형 runtime** 으로 정리하기 위한
> **단일 설계 SSoT** 다.
>
> 코드 변경은 본 문서 land 후의 별도 PR 들이 한다. 본 PR 은 **설계 문서
> + 정책 계약 + 최소 scaffolding** 까지만 land 한다.

## 0. 한 줄 요약

- **gateway** = 외부 인입 + status/approval **surface** owner. 기술 결정
  은 하지 않는다.
- **tech-lead** = **technical approval owner**. council 결과를 검토하고
  실행 packet 을 만든다.
- **role council** = 한 role 안의 **3-seat 토의** (owner / challenger /
  reviewer). 외부에는 1 role.
- **cross-role synthesis** = role council 들이 끝난 뒤에만 진입.
- 사람 승인이 필요한 L3/L4 행동은 그대로 유지. operator action inbox 도
  그대로.

## 1. 왜 필요한가

지금까지의 흐름은 단일 deliberation 에서 한 role 당 1 take 가 한 번에
나오는 구조였다 (`agents/deliberation.py`, `policies/runtime/agents/
engineering-agent/team-conversation.md` §7). 이 모델은 다음 한계를 갖는다.

1. **같은 전공 안의 반대 의견이 표면화되지 않는다.** 한 role 의 발화가
   곧 그 role 의 합의로 취급된다. owner / challenger 가 분리되지 않으면
   "다 같이 잘하자" 식 합의가 cross-role synthesis 로 그대로 올라간다.
2. **provider 가 늘면 role 수도 늘어난다.** Claude / Codex / Gemini /
   Ollama 를 각각 별 role 처럼 등록하면 council 이 폭증한다.
3. **technical approval 과 operator approval 이 한 surface 에서 섞인다.**
   `#승인-대기` 카드가 "이 설계가 맞는가" 와 "외부 사실 / secret /
   배포" 를 동시에 묻게 된다. 둘은 owner 가 다르다.
4. **review feedback 이 lifecycle 안으로 잘 안 돌아온다.** review-loop
   은 PR 단계에 정의되어 있지만, execution 직후의 self-review (실행 결과
   를 council 이 다시 확인) 와 retrospective 가 lifecycle 의 1급 stage 가
   아니다.
5. **disagreement 가 escalation 으로 잘 안 올라간다.** 2 라운드를 돌고
   도 합의가 안 되면 어디로 escalation 되는지가 명시적이지 않다.

본 설계는 이 5 가지 한계를 풀되, [`CLAUDE.md`](../CLAUDE.md) 의 hard rail
(single executor / approval matrix / operator action inbox / secret hard
rails / protected branch / no auto-merge) 을 절대 약화하지 않는다.

## 2. 현재 구조와의 차이

| 영역 | 현재 | 본 설계 |
|---|---|---|
| 한 role 의 의견 | `RoleTake` 1 개 (deliberation.py) | `RoleCouncilResult` = `RoleDraft × N seats + PeerReviewNote × M` |
| seat 구분 | 없음 | `owner` / `challenger` / `reviewer` (3-seat 기본) |
| provider 수와 role 수 | 1:1 (provider 늘면 role 처럼 동작) | 직교 — provider 는 seat 의 **realizer**. role 수 불변 |
| cross-role synthesis 진입 | role 발화 끝나면 바로 | 모든 role council 이 `consensus_status ∈ {agreed, agreed_with_conditions, escalated}` 일 때만 |
| disagreement 처리 | 발화에 묻힘 | 2 라운드 토론 후 `disagreement_summary` 와 함께 tech-lead 로 escalate |
| approval surface | gateway 가 `#승인-대기` 단일 표면 | tech-lead = **technical approval** (council → ApprovalPacket), gateway = **operator approval** (`#승인-대기` 5 종 카드) |
| approval status | `approve` / `reject` | `approve` / `approve_with_conditions` / `reject` / `escalate` |
| execution 직후 검토 | review-loop 이 PR 단계에서만 | `ExecutionReview` stage 가 lifecycle 의 1급 — review feedback 이 다시 council 또는 reopen 으로 분기 |
| Discord surface | role take 본문 그대로 dump | council 의 `public_summary` 만. 내부 토의 raw 는 vault / `session.extra` 에만 |

## 3. Role Council 모델

### 3.1 3-seat 기본 구조

한 role 의 council 은 기본 **3 seat** 으로 구성된다. seat 은 책임 역할
이며, provider (Claude / Codex / Gemini / Ollama) 와는 직교한다.

| seat | 책임 |
|---|---|
| `owner` | 이 role 의 1차 draft 작성. 자기 도메인의 입장과 근거. |
| `challenger` | 같은 role 이 놓치기 쉬운 반대 / 회의론 / 위험. owner draft 를 반증한다. |
| `reviewer` | 두 입장을 합쳐 council 의 `PeerReviewNote` 작성. 합의 / 보강 필요 / escalate 판단. |

`provider` 가 늘어도 seat 는 3 개로 유지된다. 한 seat 에 여러 provider 의
의견이 합쳐질 수 있지만, council 외부에는 한 seat 의 결정만 노출된다.

### 3.2 seat 결정 규칙

- `owner` 는 해당 role 의 **default executor priority** 에 매치되는 provider.
- `challenger` 는 owner 와 **다른** provider 를 우선 선택. provider 가
  1 개 뿐이면 같은 provider 의 다른 prompt mode (예: red-team 모드) 로 대체.
- `reviewer` 는 owner / challenger 의 결과를 입력으로 받아 결정만 한다 —
  새 draft 를 만들지 않는다.

### 3.3 council 결과

각 role council 은 한 번의 토의 round 가 끝나면 다음을 산출한다.

```text
RoleCouncilResult
├── role: str                          # "backend-engineer" 같은 정규화 주소
├── round_index: int                   # 1-based
├── drafts: tuple[RoleDraft, ...]      # seat 별 draft (owner / challenger)
├── peer_review: PeerReviewNote        # reviewer 의 종합
├── consensus_status: enum             # agreed / agreed_with_conditions /
│                                      # needs_another_round / escalated
├── disagreement_summary: str?         # status != agreed 일 때 채움
└── public_summary: str                # Discord 등 외부 노출용 (raw 토의 X)
```

### 3.4 2-라운드 cap → escalation

같은 role council 안에서 2 라운드를 돌고도 `consensus_status` 가
`agreed` / `agreed_with_conditions` 가 아니면 **escalate**.

- `consensus_status = escalated`
- `disagreement_summary` = 어떤 점에서 합의가 안 됐는지 + 결정 후보 목록
- escalation 수신자는 **tech-lead**. (gateway 가 아님.)

이 규칙은 [`team-conversation.md`](../policies/runtime/agents/engineering-agent/team-conversation.md)
§4 의 chain 모델을 **대체하지 않고 확장**한다.

## 4. Lifecycle 확장

### 4.1 기존 13 stage 는 그대로 유지

[`lifecycle-mvp.md`](../policies/runtime/agents/engineering-agent/lifecycle-mvp.md) §3
의 13 stage 는 그대로 유지된다. `intake → triage → role_selection →
research_planning → role_scoped_research → sufficiency_check →
deliberation → synthesis → interim_report → insufficient_report →
final_report → obsidian_preview → obsidian_recorded` + optional
`coding_authorization_pending → coding_job_ready`.

### 4.2 substage 와 extra key 만 추가

`deliberation` 과 `synthesis` 사이에 **council substage** 가 들어간다.
top-level stage 는 그대로 `deliberation` 으로 유지하고, `session.extra
["lifecycle_substage"]` 와 `session.extra["role_councils"]` 가 세부 상태를
잡는다.

```text
deliberation
├── substage: role_brief_distributed     ← TaskBrief + RoleWorkOrder 발행
├── substage: role_drafts_in_progress    ← seat 별 RoleDraft 수집
├── substage: peer_review_pending        ← reviewer 가 PeerReviewNote 만드는 중
├── substage: council_round_complete     ← 1 라운드 끝
├── substage: council_escalated          ← 2 라운드 cap 후 tech-lead 로
└── substage: council_ready_for_synthesis ← 모든 role council 결과 OK
synthesis  (← 기존 stage. council 결과 입력)
├── substage: tech_lead_synthesis        ← TechLeadSynthesis (기존)
├── substage: approval_packet_drafted    ← ApprovalPacket 작성
└── substage: approval_surface_posted    ← gateway operator surface 게시
```

새로운 lifecycle stage 도 1개 추가된다 — `execution_review`. coding 또는
docs write 가 끝난 직후에만 진입한다.

```text
execution_review
├── substage: ci_signal_received
├── substage: role_council_reconvened    ← 결과를 다시 council 에 확인
├── substage: review_feedback_routed     ← review_loop 또는 reopen
└── substage: retrospective_candidate    ← RetrospectiveCandidate stamp
```

### 4.3 `session.extra` 키 확장

기존 [`lifecycle-mvp.md`](../policies/runtime/agents/engineering-agent/lifecycle-mvp.md)
§4 표에 다음 키만 추가된다 (기존 키는 그대로).

| key | writer | meaning |
|---|---|---|
| `lifecycle_substage` | council/synthesis 모듈 | 현재 substage id (위 표) |
| `task_brief` | tech-lead triage | `TaskBrief` payload (목적 / 범위 / 제외 / role 별 work order seed) |
| `role_work_orders` | tech-lead triage | role → `RoleWorkOrder` |
| `role_councils` | council runner | role → `RoleCouncilResult` (round 별 list) |
| `approval_packet` | tech-lead synthesis | `ApprovalPacket` payload (council pass + tech-lead signoff) |
| `tech_lead_signoff` | tech-lead | `{signed_off_by, signed_off_at, conditions}` |
| `execution_reviews` | review_loop | `ExecutionReview` list |
| `retrospective_candidates` | post-execution | `RetrospectiveCandidate` list (자동 작성 금지, 후보만) |

모든 값은 `to_json_safe` 통과해야 한다 (기존 정책).

## 5. Approval Flow (technical vs operator)

### 5.1 분리

| 종류 | owner | 무엇을 결정 | 어디서 묻나 |
|---|---|---|---|
| **technical signoff** | tech-lead | "이 설계 / 계약 / 변경 범위가 맞는가" — council 결과 충분, scope/forbidden_scope/test 전략 모두 정의됨 | 본 runtime 내부 — gateway 가 아님. `ApprovalPacket.tech_lead_signoff` 가 SSoT. |
| **operator approval** | gateway → 사용자 | autonomy ladder L3/L4, INFO/ACCESS/SECRET/DECISION 5 종 | `#승인-대기` 카드 — 기존 surface |

**경계 규칙:**

- tech-lead 가 **operator approval 을 대신 결정하지 않는다.** `secret`
  / `deploy` / `merge` 같은 L4 가 필요하면 항상 gateway 의 operator
  inbox 카드.
- gateway 가 **technical signoff 를 대신 결정하지 않는다.** council 결과
  가 부족하면 카드 게시 자체를 차단 — `#승인-대기` 에 reach 하지 않는다.
- 두 결정은 같은 lifecycle 안에서 직렬화될 수도, 병렬일 수도 있다.
  예: tech-lead signoff 가 끝났지만 operator 가 SECRET_REQUIRED 응답
  대기 → tech-lead 는 ready, gateway 는 waiting.

### 5.2 ApprovalPacket 구조

```text
ApprovalPacket
├── task_brief_ref: str                # session_id + brief revision
├── role_council_results: tuple[RoleCouncilResult, ...]
├── tech_lead_signoff:
│     status: enum [signed_off, conditional, blocked]
│     conditions: tuple[str, ...]
│     rationale: str
│     signed_off_by: str               # role address "engineering-agent/tech-lead"
│     signed_off_at: datetime
├── operator_requests: tuple[OperatorActionRef, ...]  # 5 종 카드 참조
├── executor_role: str                 # 단일 executor (기존 hard rail)
├── write_scope: tuple[str, ...]
├── forbidden_scope: tuple[str, ...]
├── test_strategy: str
├── rollback_plan: str
└── status: enum [draft, ready, conditional, escalated, archived]
```

### 5.3 조건부 승인 (`approve_with_conditions`)

`tech_lead_signoff.status = conditional` 의미는:

- council 결과는 OK 지만, 실행 중 만족해야 할 조건이 있다 (예: "회귀
  테스트 신규 케이스 N 개 이상", "feature flag off 상태로 land").
- 조건이 `forbidden_scope` 또는 새 operator action 을 요구하면 그 카드도
  같은 packet 에 첨부.
- 조건이 자동 검증 불가능하면 `qa-engineer` 에게 자동 verify task 위임.

이 상태는 단일 executor 원칙을 깨지 않는다 — 조건은 owner role 이 실행
중에 만족해야 하는 hard gate 일 뿐.

### 5.4 Status 전이

```text
ApprovalPacket.status:

draft
  └── council 결과 충돌 ─▶ escalated  (tech-lead 가 다시 토의 요청)
  └── tech-lead 검토 끝  ─▶ ready / conditional
                                └── operator action 응답 대기 ─▶ same status
ready / conditional
  └── execution_review 진입 ─▶ archived
escalated
  └── 사용자가 결정 / scope 축소  ─▶ draft (재토의)
```

## 6. Execution Review / Retrospective Flow

### 6.1 ExecutionReview

execution 이 끝나면 (PR draft + CI signal 또는 docs write 완료) 같은
council 멤버가 결과를 검토한다. 새 lifecycle stage `execution_review`
의 산출물.

```text
ExecutionReview
├── packet_ref: str                    # 위 ApprovalPacket
├── ci_status: enum                    # green / red / partial / not_applicable
├── role_council_recheck: tuple[RoleCouncilRecheck, ...]
│      ├── role
│      ├── status: ok / minor / needs_rework
│      └── notes
├── reviewer_role: str                 # 보통 tech-lead, 위임 가능
├── follow_up_actions: tuple[str, ...]
└── decision: enum [
    accept_and_close,                  # 완료
    accept_with_followups,             # 작업 자체는 OK, 후속 작업 새로 생성
    reopen_for_rework,                 # 같은 council 재진입 (round_index 누적)
    reroute_to_review_loop,            # review-loop.md 경로 (PR 코멘트 기반)
]
```

### 6.2 Reopen 흐름

`decision = reopen_for_rework` 일 때:

- `session.extra["lifecycle_substage"]` = `role_drafts_in_progress` 로
  되돌리지 않고, **새 council round** 를 추가한다 (`round_index += 1`).
  이전 결과는 보존.
- 같은 executor role 이 새 round 의 결과로 다시 실행한다 — 단일 executor
  원칙 그대로.
- `ApprovalPacket.status = escalated` (다시 신호프 필요) 또는 `draft`
  (tech-lead 가 새 packet 작성).

### 6.3 Review-loop 과의 관계

`decision = reroute_to_review_loop` 일 때:

- `review_loop.py` 의 기존 `record_review_feedback` 가 입력을 받는다.
- 라우팅은 review-loop §"재분배 규칙" 그대로 — primary_role 한 명이 응답.
- 단, **PR 외부 origin** (CI failure / test regression / production
  incident) 도 `ReviewSource.EXTERNAL_AGENT` 로 받을 수 있게 surface 확대.

### 6.4 RetrospectiveCandidate

`execution_review` 가 끝나면 `RetrospectiveCandidate` 가 만들어진다 —
자동 작성이 아니라 **후보**. [`self-improvement-flow.md`](../policies/runtime/agents/engineering-agent/self-improvement-flow.md)
§3 의 운영자 안내 흐름을 그대로 따른다.

```text
RetrospectiveCandidate
├── session_id
├── source: enum [council_disagreement, ci_failure, ok_with_followups, postmortem]
├── candidate_topic: str
├── why_candidate: str                 # 회고 후보로 잡힌 이유
├── proposed_keep / proposed_problem / proposed_try: tuple[str, ...]
└── status: pending  (운영자가 promote 해야 retrospective note 작성 가능)
```

## 7. 단계별 Rollout Plan

본 설계는 한 PR 에 다 land 시키지 않는다. 6 단계로 쪼갠다.

| Phase | scope | 산출물 |
|---|---|---|
| **C1 — 본 PR** | 설계 문서 + 정책 갱신 + 최소 scaffolding (enum / type / helper) + 테스트 TODO | `docs/engineering-role-council-runtime.md`, lifecycle-mvp / team-conversation / message-protocol / review-loop / self-improvement-flow 갱신, `agents/deliberation.py` / `messaging/message.py` / `lifecycle/session_status.py` / `review_loop.py` 신규 enum, `tests/engineering/test_role_council_contracts.py` |
| **C2** | TaskBrief / RoleWorkOrder 생성기 + `role_councils` substage runner (deterministic seat fanout) | router 진입 substage 분기, peer review reviewer 결정 lookup |
| **C3** | 2-라운드 cap + disagreement summary + tech-lead escalation 흐름 | council escalation tests |
| **C4** | ApprovalPacket land + `tech_lead_signoff` / `conditional` 상태 + gateway operator surface 분리 | approval_matrix.md 의 카드 5 종과 packet 의 mapping 표 명문화 |
| **C5** | ExecutionReview stage 실 구현 — review_loop reroute, council recheck | `tests/engineering/test_execution_review.py` |
| **C6** | RetrospectiveCandidate land — self-improvement-flow §3 의 신호 surface 와 wiring | 이미 land 된 retrospective note kind 와 cross-link |

C1 의 hard 약속 — **자율 정책 수정 루프 만들지 않음**. council 결과가
`policies/` 를 자동으로 patch 하는 코드 경로 자체가 없다. self-improvement
는 candidate 만 만들고 운영자가 수동 promote.

## 8. Hard rail 유지 (요약)

- single executor 원칙 — `ApprovalPacket.executor_role` 가 단일 role.
- approval matrix L3/L4 — 그대로. `tech_lead_signoff` 가 operator
  approval 을 대체하지 않는다.
- operator action inbox — `#승인-대기` 카드 5 종 (APPROVAL / INFO /
  ACCESS / SECRET / DECISION) 그대로.
- secret / deploy / merge / main 직접 push 금지 — 그대로.
- protected branch 직접 push 차단, force push 금지 — 그대로.
- self-improvement 자동화 금지 — candidate 만, promote 는 운영자.
- Discord 내부 토의 dump 금지 — `public_summary` 만 surface.

## 9. 참고 / 동기화

본 문서를 추가하면 다음만 갱신한다 (중복 회피).

- [`AGENTS.md`](../AGENTS.md) §2 — "engineering-agent 부서 council 구조"
  행 추가
- [`CLAUDE.md`](../CLAUDE.md) — runtime governance hard rails 의 cross-link
- [`policies/runtime/agents/engineering-agent/lifecycle-mvp.md`](../policies/runtime/agents/engineering-agent/lifecycle-mvp.md)
  §3 / §4 — substage + extra key
- [`policies/runtime/agents/engineering-agent/team-conversation.md`](../policies/runtime/agents/engineering-agent/team-conversation.md)
  §7 — RoleTake 위에 3-seat council 표기
- [`policies/runtime/agents/engineering-agent/message-protocol.md`](../policies/runtime/agents/engineering-agent/message-protocol.md)
  §4 — RequestedAction 에 `peer_review` / `council_synthesis` 추가
- [`policies/runtime/agents/engineering-agent/review-loop.md`](../policies/runtime/agents/engineering-agent/review-loop.md)
  — execution_review 입력 경로 명시
- [`policies/runtime/agents/engineering-agent/self-improvement-flow.md`](../policies/runtime/agents/engineering-agent/self-improvement-flow.md)
  — RetrospectiveCandidate kind 명시
- [`agents/engineering-agent/manifest.json`](../agents/engineering-agent/manifest.json)
  + role manifest — `council_seats` / `peer_review` / `deliverable_schema`
  필드 도입
