---
id: role-mistake-preflight
title: 역할별 반복 실수 preflight 게이트
fires_on: coding_authorization_pending
phase: pre
sync: advisory
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - backend-engineer
  - frontend-engineer
  - qa-engineer
  - product-designer
  - devops
output_contract:
  - verdict: pass | advisory | warning | block
  - role_id: str
  - action: str
  - triggered_mistake_keys: list[str]
  - checklist: list[str]
  - headline: str
side_effects:
  - 운영자/다음 worker 프롬프트에 preflight checklist 노출
  - block 판정 시 lifecycle 단계 진입 보류 (gateway 가 사유 surface)
preconditions:
  - workflow_session 존재
  - role_id 와 action(예: coding_execute / discussion_handoff)이 정해져 있음
references:
  - apps/engineering-agent/src/yule_engineering/agents/lifecycle/mistake_ledger.py
  - apps/engineering-agent/src/yule_engineering/agents/lifecycle/preflight_judgement.py
  - apps/engineering-agent/src/yule_engineering/agents/lifecycle/hook_candidate.py
  - apps/engineering-agent/src/yule_engineering/agents/lifecycle/mistake_surface.py
related_skills: []
---

# Hook: role-mistake-preflight

> **현재 단계:** Foundation 정의 + pure-python seam + tests. 자동 호출 dispatcher 는 후속 PR (이슈 #81 의 후속 live wiring).

## 트리거 조건

다음 모두를 만족하는 시점에 fire (= 역할별 작업 진입 직전 evaluator):

1. workflow_session 의 lifecycle stage 가 다음 중 하나로 이동 직전:
   - `coding_authorization_pending` / `coding_job_ready` (coding-execute 직전)
   - `obsidian_preview` 의 discussion handoff
   - 이 외에도 역할이 명시적으로 결정된 모든 분배 시점에서 호출 가능
2. session 에 대해 role_id 가 정해져 있다 (예: backend-engineer, qa-engineer, devops 등).
3. session.extra 의 `role_mistake_ledger` 가 비어 있지 않다 (없으면 즉시 PASS).

## 동작

```
역할 분배 직전
    │
    ▼
[1] mistake_ledger 에서 role_id 의 mistakes_for_role 호출
    │
    ▼
[2] preflight_judgement.evaluate_preflight 호출 — verdict 산출
    │
    ▼
[3] verdict 별 routing
    │
    ├─ pass     → 통과 (signal 없음)
    ├─ advisory → 다음 worker 프롬프트 / 운영자 surface 에 checklist 추가
    ├─ warning  → 위 + #봇-상태 markdown block 에 강조
    └─ block    → lifecycle 진행 보류 + 게이트웨이가 사유 surface
```

## 차단 / 통과 매트릭스

| 조건 (occurrence_count, severity) | verdict | 사용자/operator 응답 |
| --- | --- | --- |
| 0회 (해당 role 의 mistake 없음) | ✅ pass | (응답 없음) |
| 2회 이상 (모든 severity) | 💡 advisory | 다음 worker 프롬프트에 prevention checklist 첨부 |
| 3회 이상 (모든 severity) | ⚠️ warning | advisory + `#봇-상태` 게시판 강조 |
| 5회 이상 (모든 severity) | ⛔ block | lifecycle 보류 + "같은 실수가 5회 발생했습니다" surface |
| 3회 이상 + severity = high | ⛔ block | 위와 같음 (예: protected branch push 누적) |

threshold 는 `PreflightThresholds` 로 호출 측에서 바꿀 수 있다.

## 실패 시 routing

- `verdict = block` 이면:
  1. lifecycle 단계는 직전 stage 로 보류, 게이트웨이가 운영자에게 "어떤 mistake_key 가 한도를 넘었는지" 알린다.
  2. 운영자가 hook 후보 promotion 을 결정하면 `hook_candidate.promote_postmortem_to_hook_candidate` 로 deterministic id 를 받아 후속 hook markdown 을 만든다.
  3. 사용자가 명시적으로 override 하면 `L2_AUTO_POST_REPORT` autonomy_policy 로 강제 (자동 audit 필수).
- `verdict ∈ {advisory, warning}` 이면 lifecycle 진행, surface 에 checklist 만 추가.

## 관련 audit 기록

본 hook 자체는 read-only seam. 다음 audit 가 함께 남는다:

- 진입 시점에 `agent_ops_audit` 에 (action=preflight_judgement, autonomy_level=L1_AUTO_RECORD_REQUIRED, summary="role={role_id} action={action} verdict={verdict}") 기록 (live wiring 후속 단계).
- 차단 시 `coding_execute` 직전이면 게이트웨이가 사용자 surface 발화도 함께 audit.

## 코드 수준 enforcement

본 markdown 은 정책 명시화. 실제 코드 측면 enforcement 는 다음에 존재한다:

- `apps/engineering-agent/src/yule_engineering/agents/lifecycle/mistake_ledger.py::record_mistake / read_mistake_ledger` — ledger 기록/조회
- `apps/engineering-agent/src/yule_engineering/agents/lifecycle/preflight_judgement.py::evaluate_preflight` — verdict 산출 (pure-python, side-effect 없음)
- `apps/engineering-agent/src/yule_engineering/agents/lifecycle/hook_candidate.py::promote_record_to_hook_candidate` — 반복 실수 → 후보 hook 승격
- `apps/engineering-agent/src/yule_engineering/agents/lifecycle/mistake_surface.py::build_operator_surface` — 운영자 surface (preflight + ledger + 후보 hook 합본)

이 hook 의 dispatcher 가 land 하면 위 함수들을 호출하는 wiring 만 추가하면 됨. 새 enforcement 코드 작성 불필요.

## Hard rails

- 본 hook 은 "역할별 반복 실수" 를 surface 하는 뉴얼 게이트지 secret / branch protection 같은 1 차 line 이 아니다. 1 차 line 은 그대로 `autonomy_policy + github_writer + redact_secret_like` 가 책임.
- `block` 판정도 silent failure 없이 한국어로 사유를 surface 한다.

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| 반복 실수 count 누적 + threshold 격상 | `tests/agents/test_mistake_ledger.py`, `tests/agents/test_preflight_judgement.py` | ✅ 통과 |
| postmortem → hook candidate 변환 | `tests/agents/test_hook_candidate.py` | ✅ 통과 |
| operator surface render | `tests/agents/test_mistake_surface.py` | ✅ 통과 |
| dispatcher 가 본 hook 을 lifecycle stage 직전에 호출 | (후속 live wiring PR) | ⏳ 대기 |
