---
id: research-first-gate
title: research-first 게이트 (deliberation 진입 전 research_status 강제)
fires_on: deliberation
phase: pre
sync: blocking
owner_role: tech-lead
applicable_roles:
  - tech-lead
output_contract:
  - blocked: bool
  - reason: str
  - audit_entry: AgentOpsEntry
side_effects:
  - 사용자에게 차단 사유 응답 (gateway 가 발화)
  - agent_ops_audit 에 (action=research_first_gate, autonomy_level=L1_AUTO_RECORD_REQUIRED) 기록
preconditions:
  - workflow_session 존재
  - lifecycle stage 가 deliberation 이전
references:
  - policies/runtime/agents/engineering-agent/ecc-foundation.md
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
  - src/yule_orchestrator/agents/lifecycle_status.py
related_skills:
  - skills/research-collect.md
---

# Hook: research-first-gate

> **현재 단계:** Foundation 정의 layer. 자동 호출은 후속 PR (이슈 #25 의 후속 dispatcher).
> **단일 owner:** 본 hook 의 발화·결정 주체는 `engineering-agent / tech-lead`.

## 트리거 조건

다음 모두를 만족하는 시점에 fire (= deliberation 단계 직전 evaluator):

1. workflow_session 의 lifecycle stage 가 `deliberation` 으로 이동 직전.
2. session 이 다음 중 하나에 해당:
   - prompt 가 `[Research]` prefix 포함
   - prompt 가 research-only 키워드 포함 (`조사해줘` / `자료 수집` / `리서치만`)
   - `coding_required = True` (deterministic role check)
3. `session.extra.research_status` 가 `ready` 또는 `insufficient` 가 아니면 (= missing 이면) **blocking**.

## 동작

```
deliberation 진입 직전
        │
        ▼
[1] session.extra.research_status 조회
        │
        ▼
[2] research_status ∈ {ready, insufficient} ?
        │
        ├─ Yes → 통과 (advisory audit 기록 + 진행)
        │
        └─ No  → 차단 (blocked=true)
                  │
                  ▼
        [3] gateway 가 사용자에게 차단 사유 안내
                  │
                  ▼
        [4] agent_ops_audit 에 entry 기록
                  │
                  ▼
        [5] lifecycle 단계는 sufficiency_check 또는 role_scoped_research 로 되돌림
```

## 차단 / 통과 매트릭스

| 조건 | 결과 | 사용자 응답 |
| --- | --- | --- |
| research_status = `ready` | ✅ 통과 | (응답 없음 — deliberation 진행) |
| research_status = `insufficient` + work_report.status = `interim` | ✅ 통과 (with interim 라벨) | (work_report 헤더에 INTERIM 라벨) |
| research_status = `insufficient` + missing_roles 비어 있지 않음 | ⚠️ 통과 (deliberation 가능, 단 final 금지) | 본문에 `역할 토의 미완료 — {role} 자료 부족` |
| research_status missing AND coding_required=true | ⛔ 차단 | "코드 수정 권한 제안 전에 자료 수집이 먼저 필요해요. `자료 더 모아줘` 라고 답해 주세요." |
| research_status missing AND research-only 모드 | ⛔ 차단 | "research-only 작업이라 자료 수집을 먼저 끝내야 해요. 잠시만요." |
| research_status missing AND prompt = `[Research]` prefix | ⛔ 차단 | (위와 같음) |
| `research_forum_thread_id` 미연결 | ⚠️ 경고만 (통과) | status diagnostic 에 forum 미연결 경고 |

## 실패 시 routing

- `blocked = true` 이면:
  1. lifecycle 단계는 `role_scoped_research` 로 자동 되돌림.
  2. `auto_collect_or_request_more_input` 재호출 (skill: `research-collect`).
  3. 사용자에게 위 표의 응답 문구로 안내 — gateway 만 발화.
  4. 사용자가 `여기까지` / `deliberation 으로` 발화하면 본 hook 의 차단을 해제하고 `research_status=insufficient` 로 stamp + INTERIM 라벨로 진행.

- `blocked = false` 이면 advisory audit 만 남기고 진행.

## 관련 audit 기록

- 통과: `AgentOpsEntry(action=research_first_gate, autonomy_level=L1_AUTO_RECORD_REQUIRED, summary="deliberation 진입 허용 — research_status=ready/insufficient", outcome="passed")`
- 차단: `AgentOpsEntry(action=research_first_gate, autonomy_level=L1_AUTO_RECORD_REQUIRED, summary="deliberation 차단 — research_status missing", outcome="blocked", references=[session_id])`
- 사용자 override: `AgentOpsEntry(action=research_first_gate_override, autonomy_level=L2_AUTO_POST_REPORT, summary="사용자가 차단 해제, INTERIM 라벨로 진행", outcome="overridden")`

## 코드 수준 enforcement (이미 존재)

본 markdown 은 정책 명시화. 실제 코드 측면 enforcement 는 이미 다음에 존재한다:

- `src/yule_orchestrator/agents/lifecycle_status.py::compute_lifecycle_status` — work_report status 평가
- `src/yule_orchestrator/agents/lifecycle_status.py::can_generate_final_work_report` — research_status 가 missing 이면 final 금지
- `src/yule_orchestrator/agents/lifecycle_status.py::can_write_obsidian_record` — obsidian write gate

본 hook 의 dispatcher 가 land 하면 위 함수들을 호출하는 wiring 만 추가하면 됨. 새 enforcement 코드 작성 불필요.

## Hard rails

- 본 hook 의 차단 사유는 **사용자에게 한국어로 명확** 하게 전달. silent failure 금지.
- 사용자 override 는 `L2_AUTO_POST_REPORT` 로 강제 (자동 audit 필수).
- secret / token / pem 출력 금지 — autonomy_policy + redact_secret_like 가 1 차 책임.

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| `compute_lifecycle_status` 의 INSUFFICIENT 분기 | `tests/agents/test_lifecycle_status.py` (기존) | ✅ 통과 |
| `can_generate_final_work_report` 의 research_status missing | 같음 | ✅ 통과 |
| hook markdown 의 frontmatter schema | (후속 PR 의 loader 와 함께 추가) | ⏳ 대기 |
| dispatcher 가 본 hook 을 deliberation pre 시점에 호출 | (후속 PR) | ⏳ 대기 |
