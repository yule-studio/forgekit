---
id: research-collect
title: 자료 수집 (research collect)
owner_role: tech-lead
applicable_roles:
  - tech-lead
  - ai-engineer
  - backend-engineer
  - frontend-engineer
  - devops-engineer
  - qa-engineer
  - product-designer
autonomy_level: L1_AUTO_RECORD_REQUIRED
input_contract:
  - prompt
  - active_research_roles
  - research_budget_tier   # small / medium / large / deep_research
output_contract:
  - research_pack          # ResearchPack dict snapshot
  - research_status        # ready | insufficient | missing
  - research_source_count
  - research_stop_reason
  - research_missing_roles
preconditions:
  - session.extra contains research_forum_thread_id (forum mode)
  - role_selection has run (active_research_roles non-empty)
  - reference budget tier resolved
side_effects:
  - session.extra.research_pack written via persist_research_artifacts
  - session.extra.research_status / research_source_count / research_stop_reason set
  - agent_ops_audit entry recorded (action=user_ordered_research)
  - Obsidian research note candidate (10-projects/<project>/research/<date>_research_<slug>.md)
references:
  - policies/runtime/agents/engineering-agent/lifecycle-mvp.md
  - policies/runtime/agents/engineering-agent/research-budget.md
  - policies/runtime/agents/engineering-agent/role-profiles.md
  - apps/engineering-agent/src/yule_engineering/agents/research_collector.py
  - apps/engineering-agent/src/yule_engineering/agents/research_sufficiency.py
related_hooks:
  - hooks/research-first-gate.md
---

# Skill: research-collect

> **현재 단계:** Foundation 정의 layer. dispatcher / 자동 호출은 후속 PR (이슈 #25 의 후속).
> **단일 owner:** 모든 호출의 발화·결정 주체는 `engineering-agent / tech-lead`. 다른 역할은 *분석 입력* 으로만 참여.

## Trigger

다음 중 하나라도 만족하면 본 skill 을 호출 후보로 한다.

- 사용자 prompt 가 `[Research]` prefix 를 포함.
- prompt 에 `조사해줘` / `자료 수집` / `리서치만` / `정리까지만` 키워드 포함 (research-only 모드).
- coding_required = True 가 deterministic role check 에서 결정됨.
- 동일 session 의 `research_status` 가 미설정이거나 `insufficient`.
- 사용자가 명시적으로 "자료 더 모아 줘" / "근거 보강" 요청.

## Workflow

```
prompt + active_research_roles
        │
        ▼
[1] decide_budget(active_roles=…)        ← reference-budget tier 결정
        │
        ▼
[2] auto_collect_or_request_more_input
        │  (Tavily / Brave 멀티 프로바이더, 역할별 query)
        ▼
[3] score_research_sufficiency           ← role 별 coverage 평가
        │
        ▼
[4] persist_research_artifacts           ← session.extra 영속
        │
        ▼
[5] agent_ops_audit (user_ordered_research, L1)
        │
        ▼
research_pack / research_status / research_source_count / research_stop_reason
```

1. `decide_budget(active_roles=…)` 로 reference budget tier 를 결정한다 (small / medium / large / deep_research).
2. `auto_collect_or_request_more_input()` 으로 active role 별 query 를 분기 실행한다. dedup 은 URL/title 기반.
3. 각 round 후 `score_research_sufficiency()` 로 role 별 coverage 점수 산출.
4. 다음 stop condition 중 하나가 만족될 때까지 round 반복:
   - 모든 active role 의 coverage ≥ threshold (`sufficient`)
   - `ENGINEERING_RESEARCH_MAX_PROVIDER_CALLS` 예산 초과 (`budget_exhausted`)
   - 4 round 연속 새 자료 수 0 (`no_progress`)
   - 사용자 결정 필요 (`user_input_needed`)
   - 모든 provider 가 실패 (`no_initial_provider_hit`)
   - 필수 source_type 미수집 (`missing_required_source_type`)
5. 결과를 `persist_research_artifacts` 로 session.extra 에 영속. 동시에 `research_status` / `research_source_count` / `research_stop_reason` / `research_missing_roles` 도 설정.
6. agent_ops_audit 에 `action=user_ordered_research` (L1) 항목 추가.

## Decision Matrix

| signal | action |
| --- | --- |
| `active_research_roles` 비어 있음 | tech-lead 만 깨운 fallback (`legacy_quartet`) — research-collect 는 deferred 또는 skip |
| 사용자가 specific source 명시 (`이 자료만 봐 줘`) | provider call 1 회로 좁힘, sufficiency 평가 생략 |
| 동일 session 에 `research_pack` 이미 ready | re-run 금지 — synthesis / deliberation 단계로 진행 |
| sufficiency 미통과 + budget 잔여 | 부족한 role 위주로 추가 round |
| sufficiency 미통과 + budget exhausted | `research_status=insufficient` 로 stamp + work_report 헤더에 INSUFFICIENT 라벨 |
| `research_forum_thread_id` 미연결 | ✋ tech-lead 가 forum thread 를 먼저 생성한 뒤 본 skill 진입. forum 미연결 시 본 skill 은 noop. |

## How to Use

### Quick Mode (현재 단계 — runtime 자동)

본 skill 의 워크플로우는 이미 `agents.research_collector.auto_collect_or_request_more_input` + `agents.research_sufficiency.score_research_sufficiency` 가 lifecycle 13 단계 안에서 자동 호출한다. 운영자가 명시적으로 호출할 필요 없음.

수동 진단:

```bash
yule engineer show --session <session_id>
sqlite3 .cache/yule/cache.sqlite3 \
  "SELECT json_extract(extra,'$.research_status'), \
          json_extract(extra,'$.research_source_count'), \
          json_extract(extra,'$.research_stop_reason') \
     FROM workflow_sessions WHERE session_id = '<session_id>';"
```

### Full Mode (후속 PR — markdown loader)

```text
[ tech-lead ] 호출:
  skill_id: research-collect
  inputs:
    prompt: "<원문>"
    active_research_roles: ["tech-lead", "ai-engineer", "backend-engineer"]
    research_budget_tier: medium
  outputs:
    research_pack: {…}
    research_status: ready | insufficient
```

후속 PR 의 dispatcher 가 본 markdown 의 frontmatter 를 읽어 `applicable_roles` 검증 + autonomy_policy gate (L1 → audit 필수) + side_effects 영속을 자동 처리한다.

## Hard rails

- **L3+ 으로 escalate 금지.** 외부 paid API 호출 / 대량 크롤링은 별도 skill 으로 분리.
- **secret 노출 금지.** API key 는 `.env.local` 의 `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY` 만 사용. 본 skill 호출 / 본문 / audit 에 key 값 기록 금지 (autonomy_policy + agent_ops_audit 의 redact_secret_like 가 1 차 책임).
- **forum 미연결 시 noop.** session.extra 에 `research_forum_thread_id` 가 없으면 본 skill 은 호출만 받고 noop + 안내 응답.

## Obsidian 기록

- 성공: `10-projects/<project>/research/<date>_research_<slug>.md` (kind=research)
- 실패: `10-projects/<project>/task-logs/<date>_task-log_<session>.md` 에 `research_pack_error` 추가

## 검증 / 회귀

| 테스트 | 위치 | 상태 |
| --- | --- | --- |
| `auto_collect_or_request_more_input` 의 budget gate | `tests/agents/test_research_collector.py` (기존) | ✅ 통과 |
| `score_research_sufficiency` 의 role 별 coverage | `tests/agents/test_research_sufficiency.py` (기존) | ✅ 통과 |
| skill markdown 의 frontmatter schema | (후속 PR 의 loader 와 함께 추가) | ⏳ 대기 |
