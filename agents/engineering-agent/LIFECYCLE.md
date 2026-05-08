# Engineering Agent — operating lifecycle

이 문서는 사용자가 Discord 에서 engineering-agent 를 실제 회사처럼 다루기 위한 운영 가이드입니다. 무엇을 입력하면 어떻게 흐르는지, 그리고 각 단계에서 사용자가 던질 수 있는 문구를 묶었습니다.

## 12단계 lifecycle

```
intake
→ triage
→ role_selection         ← Phase 1: tech-lead 가 active role 결정
→ research_planning      ← Phase 2: budget / sufficiency 가 active role 만 추적
→ role_scoped_research   ← active role 만 forum 댓글 / open-call 응답
→ sufficiency_check      ← active role 기준 stop_reason 결정
→ deliberation
→ tech_lead_synthesis
→ meeting_minutes        ← Phase 3: 회의록 deterministic 생성
→ work_report            ← Phase 4: gateway 가 Discord 에 미리보기 emit
→ obsidian_record        ← Phase 5: research / decision / meeting / work-report kind 로 저장
→ optional_coding_authorization
→ supervisor_followup
```

## Role selection 정책

tech-lead 가 작업을 받으면 active role 을 3 단계 우선순위로 고릅니다.

1. **user_explicit** — 사용자가 prompt 에 직접 role 을 명시하면 그 role + tech-lead 만 active 가 됩니다.
   - 인식되는 표현: `tech-lead`, `ai-engineer` / `ai 엔지니어`, `backend-engineer` / `백엔드`, `frontend-engineer` / `프론트엔드`, `qa-engineer` / `qa 엔지니어`, `devops-engineer` / `데브옵스`, `product-designer` / `프로덕트 디자이너` / `ux 디자이너`.
2. **tech_lead_rule** — 명시 mention 이 없으면 keyword bank 점수 (높은 점수 → tie-break 은 backend-first).
   - 핵심 키워드: ai/llm/rag/agent/memory/embedding → ai-engineer · api/auth/spring/멱등 → backend-engineer · ui/react/css/접근성 → frontend-engineer · design/wireframe/카피 → product-designer · test/회귀/qa/품질 → qa-engineer · deploy/ci/docker/supervisor/모니터링 → devops-engineer.
3. **fallback** — 두 path 모두 비어 있으면 historical 기본값 (tech-lead + ai-engineer + backend-engineer + qa-engineer).

`tech-lead` 는 모든 source 에서 항상 active. 결과는 `session.extra['active_research_roles']` + `role_selection_source` + `role_selection_reasons` 로 영속화됩니다.

## Research sufficiency / stop 정책

- **sufficient** — active role 모두 minimum source 충족.
- **budget_exhausted** — `max_provider_calls` 도달.
- **no_progress** — 두 round 연속 새 URL 없음.
- **role_rotation_exhausted** — follow-up role 큐 소진.
- **no_initial_provider_hit** — 초기 검색이 빈 결과.
- **missing_required_source_type** — 필수 source 누락 (Phase 6 이후 점진 도입).
- **user_input_needed** — 자료가 부족해 사용자 확인 필요 (Phase 6 이후 점진 도입).

부족한 active role 은 `session.extra['under_covered_roles']` 와 `CollectionOutcome.under_covered_roles` 로 표면화되어 work-report 에 그대로 인쇄됩니다.

## 산출물 (artefacts)

| 산출물 | 모듈 | 저장 위치 |
|---|---|---|
| ResearchPack | `agents/research_collector.py` | `session.extra['research_pack']` (실시간 forum 게시도 함께) |
| TechLeadSynthesis | `agents/deliberation.py` | `session.extra['research_synthesis']` |
| MeetingMinutes | `agents/meeting_minutes.py` | (요청 시) Obsidian `meeting-notes/` |
| WorkReport | `agents/work_report.py` | `session.extra['work_report']` + Discord preview + (요청 시) Obsidian `reports/` |
| CodingAuthorizationProposal / CodingJob | `agents/coding_authorization.py`, `agents/coding_job.py` | `session.extra['coding_proposal']` / `coding_job` |

## Obsidian 저장 경로 (yule-agent-vault)

```
10-projects/<project>/research/        ← ResearchPack 기반
10-projects/<project>/decisions/       ← TechLeadSynthesis 기반
10-projects/<project>/meeting-notes/   ← MeetingMinutes 기반
10-projects/<project>/reports/         ← WorkReport 기반 (Phase 5 신규)
10-projects/<project>/references/      ← URL/이미지 참고 자료
10-projects/<project>/task-logs/       ← 운영자 직접 기록
10-projects/<project>/knowledge/       ← knowledge writer
```

`<project>` 우선순위: 명시 `--project` → `session.extra["project"]` → `OBSIDIAN_DEFAULT_PROJECT` env → 하드코딩 `yule-studio-agent`.

## Coding authorization 연결

- `requires_code_change=True` 인 work-report 는 자동으로 `recommended_executor_role` 을 후보로 surface.
- 사용자가 `수정 권한 제안` 또는 `코딩 권한 제안` 이라고 답하면 `recommend_authorization` 이 정식 proposal 을 만들어 `session.extra['coding_proposal']` 에 stash.
- `수정 승인` / `코딩 진행 승인` / `구현 시작` 같은 phrase 가 들어오면 proposal → `CodingJob(status="ready")` 로 전환되어 `session.extra['coding_job']` 에 박힘.
- 명시적 차단 phrase (`코드 수정하지 말고 리서치만`, `research only` 등) 가 함께 오면 게이트가 즉시 양보합니다.

## Supervisor / status 에서 보이는 항목

`format_status_diagnostic_response` 가 보여주는 라인:

- 세션 id / 상태 / 종류
- research_pack 유무
- coding 상태 (`coding_proposal` / `coding_job` 진행 단계)
- canonical 작업 prompt + 최근 continuation prompt + 이어붙인 thread id
- 운영-리서치 forum 게시 상태 + 모드 (member-bots / gateway)
- 역할 활동 기록 (role_turns)
- research loop 보고
- tech-lead synthesis 유무
- **활성 role + 선정 source** (Phase 4)
- **업무 보고서 작성 여부 + 자료 N건 + 코드 수정 여부 + stop_reason** (Phase 4)
- diagnose_session 시그널 (severity 별 정렬)

## Discord 에서 사용할 수 있는 문구 모음

### 새 작업 / 라우팅
- `[Research] 하네스 엔지니어링 도입 검토 — 운영-리서치에 자료 모아줘` — 새 research 요청
- `tech-lead / ai-engineer / qa-engineer 관점에서 정리해줘` — user_explicit role 선정
- `백엔드 + qa 엔지니어 관점에서 결제 멱등성 검토` — Korean alias 도 인식
- `이대로 진행` / `새 작업으로 진행` — 직전 제안을 확정
- `기존 세션 abc12345 로 이어가` — explicit session id 로 join (canonical prompt 자동 재사용)

### 라이프사이클 후속
- `이 세션 기준으로 운영 리서치 어디까지 됐어?` — status diagnostic
- `회의록 정리해줘` — MeetingMinutes 산출 + Obsidian meeting-notes/ 저장 제안
- `업무 보고서 만들어줘` — WorkReport preview 재출력
- `Obsidian에 저장해줘` — 저장 후보 (kind 추정 → 사용자 승인 → write)
- `참고 자료 더 모아줘` / `자료 더 모아줘 — 운영-리서치` — research 재시작 (active role 그대로 유지)

### 코딩 권한
- `이 결과를 바탕으로 수정 권한 제안해줘` — proposal 생성 (work-report 가 권고한 executor 우선)
- `수정 승인` / `코딩 진행 승인` / `구현 시작` — proposal → CodingJob
- `코드 수정 하지 말고 리서치만 해줘` — coding gate 즉시 양보, research 만 계속

### 차단 / 가드
- `새 작업으로 진행` 만 단독 입력 → 새 session 생성 거부 + clarification (canonical prompt 가 캐시에 없는 경우)
- gateway 가 보낸 안내문 (`좋습니다. 이대로 작업을 등록할게요…` / `자료가 부족합니다…`) 을 그대로 paste-back → 새 작업 거부, 친절한 안내

## 라이브 재테스트 체크리스트

1. `[Research] backend / qa 관점에서 결제 멱등성 검토` 입력 → status 가 `활성 role: tech-lead, backend-engineer, qa-engineer` 표시.
2. 연구 종료 후 `업무 보고서` 헤더 + 원문 + 참가자 + 자료 N건 + stop_reason 이 Discord 에 출력.
3. `이 세션 기준으로 운영 리서치 어디까지 됐어?` 를 thread 안에서 입력 → 동일 session id 의 work_report 라인이 status 에 표시.
4. `Obsidian에 저장해줘` → work-report kind 가 `10-projects/<project>/reports/` 에 propose 됨.
5. `이 결과로 수정 권한 제안해줘` → coding_proposal 이 work-report 의 recommended_executor_role 을 우선 후보로 사용.
