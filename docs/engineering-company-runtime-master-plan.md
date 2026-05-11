# Engineering Company Runtime Master Plan

> 목적: Yule Studio Agent를 단순한 업무 접수 봇이 아니라, Discord에서 기술 토의와 구현을 이어가고, GitHub/CI/Obsidian을 통해 계속 일하는 `tech-lead` 중심 개발 회사형 runtime으로 완성하기 위한 기준 문서.
>
> 이 문서는 "다음에 무엇을 만들지"보다 먼저 "무엇을 어떤 순서로, 어디까지 완성해야 하는지"를 고정한다.

## 1. 최종 목표

Yule가 도달해야 하는 최종 상태는 아래와 같다.

1. Discord에서 업무 접수뿐 아니라 기술 토의가 가능하다.
2. `tech-lead`가 긴 문맥을 유지하며 설계/조사/구현/질문 필요 여부를 판단한다.
3. 구현이 필요하면 승인 후 실제 코드 수정, 테스트, push, draft PR까지 이어진다.
4. CI 결과를 다시 읽고 retry / clarification / done / blocked 로 이어진다.
5. 모든 과정은 Obsidian에 운영 메모리로 기록된다.
6. 작업이 하나 끝나면 runtime이 다음 작업을 자동으로 선택한다.
7. 역할별 자료 수집/정형화 루프가 상시 돌아서, 요청이 오기 전에도 지식이 축적된다.

핵심 원칙: **Claude는 이 시스템을 만드는 도구이고, 완성 후에는 runtime이 Claude를 필요할 때 호출하며 계속 돌아야 한다.**

## 2. 현재 기준선

현재 레포는 아래 기초를 이미 갖고 있다.

- Discord intake / gateway 흐름
- research / role deliberation / approval 뼈대
- Obsidian export / note 정책의 일부
- GitHub App 기반 issue / draft PR 흐름 일부
- always-on runtime skeleton
- queue worker 구조
- CI 기초 설정

하지만 아직 아래는 미완성이다.

- 자유로운 기술 토의 모드
- 실제 live code editing executor
- CI failure → retry / re-plan 루프
- 상시 role-based research ingestion live wiring
- 작업 완료 후 next-task auto handoff

## 3. 현재 완성도 평가

이 수치는 운영 판단 기준으로 유지한다.

| 영역 | 현재 추정 (2026-05-11) | 이전 추정 |
| --- | --- | --- |
| 운영 골격 | 80~85% | 65~75% |
| Discord 기술 토의 능력 | 50~60% | 40~50% |
| 완전 자율 코딩 루프 | 70~80% | 45~55% |
| 역할별 자료 수집/정형화 루프 | 50~60% | 25~35% |
| 실제 회사처럼 굴러가는 종합 수준 | 60~70% | 45~55% |

2026-05-11 갱신 근거 — issue #81 통합 polish (`feature/issue-81-integration-polish`) 의 cross-axis 회귀 (3493 + 160 cases 모두 OK) 와 Round 4 / 4-bis / 4-ter / 4 마무리 / Round 4 후속 시리즈 land 결과. 자세한 결정은 [[notes/vault-mirror/10-projects/yule-studio-agent/decisions/2026-05-11_issue-81-decision-integration-polish.md]] § D-81-3 ~ D-81-7. 상한이 100% 가 아닌 이유:

- 운영 골격 상한 85% — autonomy producer / decision seam / status surface / operator actions 까지 land. 잔여: live LLM editor / live decision provider 활성화, Discord escalation alert 자동화.
- Discord 기술 토의 상한 60% — discussion_followup + context_pack + retrieval slot land. 잔여: `feature/issue-81-discussion-gateway` (commit `512ce7c`) 미머지, gateway / tech-lead 경계 가독성 PR 필요.
- 완전 자율 코딩 루프 상한 80% — dispatcher + executor live (RecordOnly) + CI orchestrator + retry guard + producer + funnel + claude subprocess seam land. 잔여: live LLM editor (운영자 승인 + cost + secret 정책 별도 PR).
- 역할별 자료 수집/정형화 상한 60% — provider registry + routing + retrieval + feed parser + role feed digest + provenance land. 잔여: urllib `BytesFetcher`, sitemap / html_list / html_detail / github_api 라이브 fetcher, `eng-research-collector` runtime service spawn, `SourceRefreshState` 영속화.
- 종합 상한 70% — 세 축이 cross-axis 회귀로 충돌 없음 확인, 그러나 #81 worktree split 3 축 (discussion-gateway / autonomy-execution / knowledge-geeknews) 미머지 + § 14 시나리오 6~8 단계가 live LLM editor 없이는 end-to-end 자동 불가.

이 문서의 목표는 위 4개 축이 서로 충돌하지 않게 나누어 100%에 수렴하도록 만드는 것이다.

## 4. `gateway` 와 `tech-lead` 경계

이 경계를 분명히 하지 않으면 외부 surface와 내부 조율이 섞여 시스템이 불안정해진다.

### 4.1 gateway 책임

`gateway`는 외부 surface 담당이다.

- Discord `#업무-접수` 첫 응답
- thread / session 생성
- intake metadata 정리
- `#봇-상태` / kickoff / closure 같은 운영 surface
- 외부 사용자에게 보이는 상태 전달
- 내부 runtime으로 job enqueue

정리하면: **gateway는 받는 입구와 바깥쪽 상태판이다.**

### 4.2 tech-lead 책임

`tech-lead`는 coordinator이자 부서장이다.

- 긴 문맥 유지
- 토의 진행
- role 관점 종합
- 설계/조사/구현 여부 판단
- 승인 요청과 handoff
- 구현 이후 결과 해석
- 다음 작업 선택

정리하면: **tech-lead는 생각하고 조율하고 이어가는 두뇌다.**

### 4.3 경계 규칙

1. 외부 사용자의 첫 접점은 gateway다.
2. 기술 토의가 시작되면 주체는 tech-lead다.
3. status/post/kickoff/closure는 gateway-mediated가 우선이다.
4. cross-role decision / 합의 / conflict resolution 은 tech-lead-mediated다.
5. 다른 role bot 확장 전까지는 실제 구현 실행도 tech-lead가 대표 주체다.

## 5. 운영 구조의 5개 레이어

### 5.1 Surface Layer

- Discord
- GitHub Issue / PR
- CI 알림
- Obsidian 기록

### 5.2 Coordination Layer

- gateway
- tech-lead runtime
- approval gate
- completion hook
- next-task selector

### 5.3 Intelligence Layer

- deterministic fast-path
- Claude classifier
- Claude discussion synthesizer
- Claude executor
- context pack builder
- relevant memory selector

### 5.4 Execution Layer

- worktree
- shell / git / gh
- test runner
- draft PR generation
- CI result consumption

### 5.5 Memory Layer

- research note
- decision note
- task-log
- report / retrospective
- role knowledge / collected references

## 6. 전체 루프 구조

이 시스템은 4개의 루프가 합쳐져야 한다.

### 6.1 Background Knowledge Loop

상시 돌아야 하는 루프.

- 역할별 자료 수집
- dedup / importance score
- source registry 기반 분류
- Obsidian / role knowledge 저장
- 요청 시 retrieval 가능한 상태로 유지

### 6.2 Discussion Loop

Discord thread 안에서 도는 루프.

- 사용자의 질문 이해
- context pack 구성
- 관련 note / issue / PR / code / sources retrieval
- 설계/조사/구현 여부 판단
- 필요하면 clarification

### 6.3 Execution Loop

구현이 필요할 때 도는 루프.

- approval
- worktree
- code edit
- tests
- commit / push
- draft PR

### 6.4 Improvement Loop

끝난 뒤 돌아야 하는 루프.

- CI success/failure 해석
- blocked reason 정리
- retry / re-plan
- retrospective
- next action 생성

## 7. 기술 토의 모드의 필수 요구사항

이 시스템은 단순 업무 접수로 끝나면 안 된다. 아래 같은 대화가 가능해야 한다.

- "이 구조 맞아?"
- "이건 devops 관점에서 어떻게 풀지?"
- "일단 조사만 할까?"
- "구현 전에 리스크부터 정리하자"

이를 위해 `discussion_mode` 가 필요하다.

### 7.1 discussion mode의 최소 상태

- `discussion`
- `research_only`
- `implementation_candidate`
- `clarification_needed`

### 7.2 discussion mode의 최소 입력

- 최근 Discord thread 요약
- session state
- 관련 Obsidian notes
- 관련 issue / PR
- 관련 코드 힌트
- role profile
- role research profile

### 7.3 discussion mode의 최소 출력

- 현재 판단 모드
- 왜 그렇게 판단했는지
- 구현 필요 여부
- 추가 질문이 필요한지
- next action

## 8. 문맥을 넓게 이해하게 만드는 핵심

문맥 이해는 모델 자체보다 입력 구조가 더 중요하다.

### 8.1 Context Pack Builder

각 요청마다 아래를 묶는다.

- current user message
- recent thread summary
- session.extra summary
- linked issue / PR summary
- relevant Obsidian notes
- related file hints
- role profile
- role research profile

### 8.2 Relevant Memory Selector

모든 note를 넣지 않는다. 요청과 관련된 note만 뽑는다.

- 동일 topic
- 같은 부서/역할
- 최근 실패/회고
- 관련 PR / issue
- 같은 domain / 같은 task_type

### 8.3 Fast-path + LLM 조합

- 명확한 요청은 deterministic path
- 애매한 요청만 Claude classifier
- 토의 중 ambiguity가 커지면 재분류

## 9. 자료 수집/정형화 루프는 필수

이 항목은 선택이 아니다. 회사처럼 굴러가려면 요청 전부터 지식이 쌓여야 한다.

### 9.1 역할별 상시 수집 대상

- backend: official docs, API, schema, auth 흐름, migration 패턴
- frontend: MDN, framework docs, accessibility, design system, browser compatibility
- qa: regression, test plan, bug pattern, edge case, acceptance criteria
- devops: CI/CD, infra, observability, rollout, rollback, runtime 운영 패턴
- tech-lead: architecture, ADR, tradeoff, dependency, risk, rollout plan

### 9.2 수집 루프의 두 단계

1. 요청 이전의 background ingestion
2. 요청 이후의 request-time retrieval

즉 "계속 수집"과 "요청 맞춤 retrieval"은 둘 다 필요하다.

### 9.3 구현 기준선

이미 존재하는 정책/코드:

- [research-collector.md](../policies/runtime/agents/engineering-agent/research-collector.md)
- [research-profiles.md](../policies/runtime/agents/engineering-agent/research-profiles.md)
- [collector.py](../src/yule_orchestrator/agents/engineering_intelligence/collector.py)

Round 4 에서 추가된 구현:

- [models.SourceAxis](../src/yule_orchestrator/agents/engineering_intelligence/models.py) — 10종 axis enum (official_docs / api_schema_auth / web_platform_framework / regression_test_plan / ci_cd_infra_observability / architecture_adr_tradeoff / ai_framework / design_system / security / release_notes_changelog).
- [source_registry](../src/yule_orchestrator/agents/engineering_intelligence/source_registry.py) — 모든 seed 가 `axes` tag 부여, `required_axes_for_role` / `axes_for_role` / `sources_for_axis` / `axis_hints_for_task_type` helper 추가.
- [scheduler.py](../src/yule_orchestrator/agents/engineering_intelligence/scheduler.py) — `compute_refresh_plan` (never / due / fresh / backoff / quota / review_required / auto_collect_disabled 7-state classifier), exponential backoff (×1/×2/×4/×8, 24h cap), `record_refresh_outcome`, `overdue_axes_for_role`.
- [providers.py](../src/yule_orchestrator/agents/engineering_intelligence/providers.py) — `LiveProviderSpec` (transport / endpoint / parser / rate_limit / requires_auth_env), `provider_spec_for(SourceEntry)` 디스패치, `LiveSourceFetcher` Protocol + `StubLiveSourceFetcher` 결정적 fake.
- [retrieval.py](../src/yule_orchestrator/agents/engineering_intelligence/retrieval.py) — `KnowledgeRecord` (lightweight projection), `score_knowledge_record` (role × axis × topic × importance × freshness), `KnowledgeRetriever` (`with_signals` 로 score 노출).
- [discussion/context_pack.py](../src/yule_orchestrator/agents/discussion/context_pack.py) — `EngineeringKnowledgeRef` slot + `knowledge_loader` / `knowledge_retriever` seam 추가. ContextPack.relevant_knowledge 가 자동으로 채워진다.

Round 4-bis 에서 추가된 구현 (provider 측 강화):

- [provider_registry.py](../src/yule_orchestrator/agents/engineering_intelligence/provider_registry.py) — 8 transport (rss / atom / sitemap / html_list / html_detail / github_releases_atom / github_api_repo_activity / manual) 각각에 한 줄씩 등록되는 `KnowledgeProviderRegistration` (provider_id / auth / fake_fetcher / live_factory / manual flag). `ProviderAuthRequirement` 가 decision classifier 와 동일한 dual gate (env_keys + enable_flag) 를 쓴다. `ProviderAvailability` 5 상태 (available / disabled_by_flag / missing_env / no_live_impl / manual_only) 가 운영자 대시보드에 그대로 노출. `default_registry()` 가 시드한 8 row 의 .env contract 가 `.env.example` 에 명시되어 있다.
- [providers.py — FakeKnowledgeProvider](../src/yule_orchestrator/agents/engineering_intelligence/providers.py) — `StubLiveSourceFetcher` (records, returns empty) 와 별도로 `source_id → items` fixture 기반의 결정적 fake. 라이브 PR 이 들어오기 전까지 모든 transport 의 fallback fetcher 로 사용된다.
- [provider_routing.py](../src/yule_orchestrator/agents/engineering_intelligence/provider_routing.py) — `route_refresh_plan(plan, *, role_id, registry, env)` 가 `RefreshPlan` 의 due/skipped 각 entry 에 transport / provider_id / availability / axes 를 붙여 `RoutedRefreshCandidate` 로 변환. `axis_priority_order(...)` 가 `overdue_axes_for_role` 결과를 받아 SECURITY 같이 비어 있는 axis 를 가진 candidate 를 우선 큐 앞으로 보낸다 (tier / availability / source_id 가 tie-breaker). `select_routed_due(...)` 한 번 호출로 plan→route→axis priority→tick quota 까지 처리.
- [.env.example #73 — Engineering knowledge provider env](../.env.example) — `YULE_KNOWLEDGE_<TRANSPORT>_LIVE_ENABLED` 8개와 `YULE_GITHUB_APP_*` 재사용 정책 명시. 모든 플래그 기본값은 false (cost-budget 검토 후 운영자가 켠다).

Round 4-ter 에서 추가된 구현 (live-ready parser + 운영 가시성):

- [feed_parser.py](../src/yule_orchestrator/agents/engineering_intelligence/feed_parser.py) — `parse_atom_bytes` / `parse_rss_bytes` / `parse_feed_bytes` 결정적 parser (xml.etree + email.utils 만 사용, urllib 무관). RSS-mode 소스가 실제로 Atom 페이로드를 내려주는 edge case 까지 sniff 로 dispatch. summary 는 `_SUMMARY_LIMIT=500` 자로 자동 truncate 되어 content_policy ("link + 짧은 인용") 자동 준수. `BytesFetcher` Protocol + `make_feed_live_factory` glue 가 별도 PR 에서 작성할 urllib 한 조각을 그대로 받아 `LiveFetcherFactory` 로 변환. `register_safe_feed_providers(registry, bytes_fetcher_factory=...)` 한 번 호출로 RSS / ATOM / GITHUB_RELEASES_ATOM 세 transport 가 동시에 라이브 전환된다.
- [provider_registry.py — availability_summary](../src/yule_orchestrator/agents/engineering_intelligence/provider_registry.py) — `ProviderAvailabilityRow` (transport / provider_id / availability / has_live_impl / manual / env_keys / enable_flag / missing_env_keys / enable_flag_set / description / notes) + `ProviderAvailabilitySummary` (`by_state` / `states_count` / `needs_attention` / `to_payload`). 운영자 대시보드는 `registry.availability_summary(env).to_payload()` 한 번 호출로 "5 transports live, 2 missing_env, 1 disabled_by_flag" 를 그대로 그릴 수 있다.
- [provider_routing.py — reasoning trail](../src/yule_orchestrator/agents/engineering_intelligence/provider_routing.py) — `RoutedRefreshCandidate` 가 `transport_reason` (왜 이 transport 가 선택됐는지: `rss + atom URL heuristic`, `manual (collection_mode=manual)` 등) 와 `availability_reason` (왜 이 availability state 가 됐는지: `missing env keys: YULE_GITHUB_APP_ID, ...`, `flag YULE_KNOWLEDGE_RSS_LIVE_ENABLED not truthy` 등) 두 필드를 추가로 가진다. `routing_reason` property 로 한 줄 요약 (`transport=…; availability=…`) 도 노출. `RefreshPlanStatus` 와 `refresh_plan_status(plan, *, role_id, registry, env)` 헬퍼는 routed candidates + registry availability_summary 를 한 번에 묶어서 background refresh planner 가 tick 마다 JSON 한 덩어리로 dump 할 수 있게 만든다.

남은 일 (별도 worktree / PR):

- urllib 기반 `BytesFetcher` 한 조각 (timeout / user-agent / 30x 처리). `register_safe_feed_providers(registry, bytes_fetcher_factory=...)` 한 번 호출로 RSS / Atom / GitHub releases atom 이 동시에 live 전환된다.
- sitemap / html_list / html_detail / github_api_repo_activity 의 live fetcher (parser 까지 미구현; 본 PR 시점에서 NO_LIVE_IMPL).
- runtime service spawn (`eng-research-collector`) — scheduler tick → `refresh_plan_status` 로 가시성 dump → `select_routed_due` → registry fetcher → adapter → vault writer.
- `SourceRefreshState` persistence (sqlite or vault sidecar).
- discussion synthesizer 가 `relevant_knowledge` slot 을 prompt 에 어떻게 짜 넣을지.

## 10. CI와 CD의 관계

현재는 PR CI가 1순위고, CD는 후순위다.

### 10.1 지금 꼭 할 것

- PR에서 테스트
- package smoke
- Discord success/failure 알림
- CI 결과를 runtime 상태로 반영

### 10.2 지금 하지 않을 것

- production deploy
- auto merge
- main push 배포
- 완전한 zero-downtime CD

### 10.3 왜 분리하나

CI는 품질 게이트고, CD는 운영 위험과 직결된다. 지금은 runtime이 로컬에서 완결 루프를 먼저 완성해야 한다.

## 11. Obsidian 운영 메모리 정책

Obsidian은 로그 저장소가 아니라 운영 메모리다.

### 11.1 note 종류

- research
- decision
- task-log
- report / retrospective

### 11.2 note 규칙

- `## 관련 문서` 필수
- wikilink 이름은 실제 basename과 일치
- 민감정보/secret/실제 key는 저장 금지
- 공유 가능한 것과 로컬 전용 것을 분리

### 11.3 저장해야 할 내용

- 어떤 자료를 참고했는지
- 어떤 판단을 했는지
- 어떤 구현이 진행됐는지
- 실패 원인이 무엇인지
- 다음 액션이 무엇인지

## 12. PR를 나누는 원칙

하나의 PR에서 모든 걸 끝내려 하지 않는다. 섹션과 milestone을 나눠서 간다.

### 12.1 PR 분리 원칙

1. 운영 골격
2. Discord 기술 토의
3. 완전 자율 코딩 루프
4. 자료 수집/정형화 루프

한 PR 안에서는 하나의 중심 목표만 다룬다. foundation PR이 크더라도 "하나의 단계 완료" 단위여야 한다.

### 12.2 worktree 원칙

하나의 initiative 아래에서도 worktree는 나눈다.

예시:

- `feature/company-runtime-discussion`
- `feature/company-runtime-execution`
- `feature/company-runtime-research-loop`
- `feature/company-runtime-autonomy`

## 13. 다음 구현 우선순위

지금부터의 실제 순서는 아래가 맞다.

### Phase 1. Discussion Mode

완료 기준:

- Discord에서 기술 토의 가능
- context pack 구성
- implementation 여부 판단

### Phase 2. Claude Decision Layer

완료 기준:

- ambiguous request만 `claude` 호출
- deterministic fast-path 유지
- discussion / research / implementation / clarification 분류

### Phase 3. Coding Executor Live Wiring

완료 기준:

- 승인 후 실제 code edit
- test
- commit/push/draft PR

### Phase 4. CI Failure → Retry Loop

완료 기준:

- CI 실패 읽기
- retry guard
- blocked / retry_ready / done 반영

### Phase 5. Background Research Ingestion

완료 기준:

- 역할별 수집이 background로 동작
- 정형화된 자료가 role knowledge로 저장

### Phase 6. Next Task Auto-Handoff

완료 기준:

- 작업 하나 끝나면 selector가 다음 작업 선택
- runtime이 계속 이어서 일함

## 14. 완료 조건

아래 시나리오가 end-to-end로 가능해야 "회사형 runtime" 완성으로 본다.

1. Discord에서 요청 또는 기술 토의 시작
2. tech-lead가 긴 문맥으로 대화
3. 관련 자료와 note를 자동 retrieval
4. 조사/구현/질문 필요 여부 판단
5. 구현이면 승인 요청
6. 승인 후 실제 코드 수정과 draft PR 생성
7. PR CI 결과 수신
8. 실패면 retry / clarification / blocked 처리
9. 성공/실패/차단 상태를 Obsidian + GitHub에 기록
10. 다음 작업을 자동 선택

## 15. Claude 실행 프로토콜

Claude는 UI 자동조작 대상으로 쓰지 않는다. runtime이 `claude` 명령을 호출하는 도구로 쓴다.

### 15.1 역할

- classifier
- discussion synthesizer
- executor

### 15.2 원칙

- 작은 단위로 호출
- structured output 우선
- fast-path가 애매할 때만 호출
- 실패하면 blocked reason 남기고 deterministic fallback

### 15.3 handoff 규칙

세션이 끊겨도 다음 세션이 이어갈 수 있어야 한다.

반드시 남길 것:

- 현재 phase
- 현재 branch / worktree / PR / issue
- 현재 blocker
- 남은 next actions
- 관련 note 링크

## 16. 운영 가드

아래는 영구 hard rail이다.

- protected branch 직접 push 금지
- force push 금지
- auto merge 금지
- production deploy 자동화 금지
- secret / token / pem 출력 금지
- 기존 사용자 변경 덮어쓰기 금지

## 16-bis. Coding executor 라이브 와이어링 (Round 3)

Phase 3 ~ 4 의 실제 코드 경로는 다음과 같이 land 했다 — 어느 모듈이 어느 책임을 가지는지 한눈에 보이게 둔다.

### 16-bis.1 producer (gateway 승인 → coding_execute 큐)

- `agents/coding/authorization.py` — 사용자 요청 → `CodingAuthorizationProposal`.
- `agents/coding/job.py` + Discord 라우터 — 승인 phrase 도착 시 `CodingJob(status=ready)` 를 `session.extra["coding_job"]` 에 영속.
- `agents/job_queue/coding_execute_dispatcher.py` (Round 3 신규) — `iter_ready_coding_jobs` / `dispatch_ready_coding_jobs` 가 `coding_job=ready` 세션을 스캔해 `coding_execute` 행을 큐에 enqueue, `session.extra["coding_execute_dispatch"]` marker 로 dedup.
- `agents/job_queue/coding_execute_dispatcher.WorkflowSessionState` — `next_task_selector` 의 `SessionStateLike` 구현. selector 의 우선순위 체인에 production 세션 데이터를 연결.

### 16-bis.2 executor (worktree → tests → commit → push → draft PR)

- `agents/job_queue/coding_executor_worker.py` — Protocol seam 6 종 (worktree / editor / tests / committer / pusher / draft PR). protected branch / force push 가드.
- `agents/job_queue/coding_executor_live.py` — `build_live_executor`: 로컬 worktree + RecordOnly editor + subprocess 테스트 + LocalGitCommitter + GithubAppPusher + GithubAppDraftPRCreator. LLM editor 는 여전히 명시적 blocker.
- `runtime/run_service.py::build_coding_executor_bundle` (Round 3 신규) — env 매트릭스 평가, GitHub App env 3종 모두 갖춰진 경우에만 LiveGithubAppClient 시도. 부분 설정으로는 절대 활성화되지 않음.
- `runtime/run_service.py::_build_process_job(CODING_EXECUTOR)` (Round 3 신규) — 서비스 등록, dispatcher 틱 → consumer pick → process_job → progress recorder 순서.

### 16-bis.3 CI 결과 → 4-state 표준화 + retry 가드

- `agents/job_queue/ci_status.py` (Round 2) — `CIStatus` / `decide_retry` / `CIRetryPolicy` (max_attempts=3 기본, ×2 backoff, 30 분 cap). pure decider.
- `agents/job_queue/ci_retry_orchestrator.py` (Round 3 신규) — `orchestrate_ci_retry` 가 GithubAppCheckRunFetcher 로 CI 조회 → `decide_retry` → retry 시 새 `coding_execute` 행 enqueue (branch_hint 에 `-attemptN` suffix 로 dedup 회피) → terminal 시 `completion_hook.record_completion` 호출 → `session.extra` 갱신.
- `github_app/live_client.py::list_check_runs` / `get_pull_request` (Round 3 신규) — orchestrator 가 의존하는 GitHub Checks API 어댑터.
- `agents/job_queue/next_task_selector.py::select_next_task_with_ci_retry_guard` (Round 2) — selector 가 max_attempts 초과 PR 을 자동으로 skip + `payload['ci_retry_escalated']` 로 surface.

### 16-bis.4 결과 surface (Obsidian + GitHub PR)

- `agents/job_queue/coding_execute_progress.py` (Round 3 신규) — `record_coding_execute_progress` 가 (1) `session.extra["coding_execute_progress"]` 50 행 capped history 에 entry 추가, (2) `obsidian_write` 큐에 `task-log` 노트 enqueue (approval 게이트 없는 kind), (3) GitHub PR comment 발사 (poster 부재 시 silent skip).
- `runtime/run_service.py::_record_coding_progress_after_outcome` — coding_execute 종료 outcome 마다 자동 호출. workflow_state 로드 실패 / GitHub 502 모두 swallow.

### 16-bis.5 환경 contract

- `YULE_CODING_EXECUTOR_AUTOSPAWN=true` — `runtime up` 이 `eng-coding-executor` 를 자동 spawn 할지 결정. 기본값은 opt-in 비활성.
- `YULE_CODING_EXECUTOR_DRY_RUN={true|false}` — dispatcher 가 만드는 request 의 dry_run override. 기본값은 metadata > env > true. `false` 명시 + GitHub App env 갖춤 = 실제 push 까지 진행.
- `YULE_CODING_EXECUTOR_REPO` / `YULE_CODING_EXECUTOR_BASE_BRANCH` — repo / base branch 기본값.
- `YULE_GITHUB_APP_ID` + `YULE_GITHUB_APP_INSTALLATION_ID` + `YULE_GITHUB_APP_PRIVATE_KEY_PATH` — 모두 갖춰져야 LiveGithubAppClient 시도.
- `YULE_CODING_EXECUTOR_REPO_ROOT` / `YULE_CODING_EXECUTOR_WORKTREE_ROOT` — worktree 위치 override (기본 `YULE_REPO_ROOT` + `/tmp/yule-coding-executor-worktrees`).

### 16-bis.6 운영 hard rails (Round 3 시점 재확인)

- protected branch (main / master / develop / dev / prod / release / release\\* / hotfix\\*) 직접 push 차단 — `coding_executor_worker.is_protected_branch`.
- force push 차단 — Pusher Protocol 에 force flag 자체가 존재하지 않음.
- LLM editor 비활성 — `RecordOnlyCodeEditor` 가 plan markdown 만 작성, 실제 source 변경 없음. 활성화는 별도 PR + 운영자 승인.
- 무한 retry 차단 — `decide_retry` 가 max_attempts 초과 시 즉시 `blocked` 로 escalate, orchestrator 는 그 verdict 그대로 신뢰.
- 부분 GitHub App env → 절대 LiveGithubAppClient 시도 안 함 (3 키 중 하나라도 비면 dry-run blocker 로 degrade).

## 16-ter. Autonomy producer / scheduler (Round 4)

Round 3 까지 사람이 메시지를 넣어야 다음 단계가 굴러갔다. Round 4 는 작업
하나가 끝난 뒤 runtime 이 스스로 다음 candidate 를 만들고 큐에 넣게 만드는
producer 계층과 충돌 가드, completion funnel, 외부 decision seam 을 land.

### 16-ter.1 producer / scheduler

- `agents/job_queue/autonomy_producer.py` — `AutonomyProducer.tick()` 가
  selector poll → 승인 coding_job → unresolved discussion → CI failure
  funnel 순서로 sub-producer 실행. 매 tick 결과는
  `AutonomyProducerReport` (dispatches / locks_held / summary_line).
  큐 직접 enqueue 는 하지 않고 기존 dispatcher 모듈 (
  `coding_execute_dispatcher`, `discussion_followup`,
  `ci_retry_orchestrator`) 위에 얇게 얹는다.
- `agents/job_queue/autonomy_lock.py` — `AutonomyLockRegistry`. branch /
  session / coding_job 스코프별 단명 advisory lock (in-memory, TTL,
  expired lazy reclaim, thread-safe). 큐 dedup 위 보강.
- `runtime/run_service.py::_build_autonomy_producer_tick` — supervisor
  branch 가 producer 를 빌드 + supervisor watch loop 의
  `autonomy_producer_tick_fn` 인자로 전달.
- `agents/job_queue/worker_loop.py::run_supervisor_watch_loop` —
  `autonomy_producer_tick_fn` / `autonomy_producer_interval_seconds` /
  `autonomy_producer_on_report` 3 개 인자 추가. status post / self
  improvement 와 같은 패턴 (interval gate, never crash, on_report hook).

### 16-ter.2 discussion jobization

- `agents/job_queue/discussion_followup.py` — `DiscussionFollowupDispatcher`
  가 unresolved discussion 모드별로 분기:
  - `discussion` + missing_roles → `role_take.enqueue` (KIND_TURN, role
    별 idempotent).
  - `research_only` → `research_collect.enqueue` (session 단위 idempotent).
  - `clarification_needed` → SKIPPED (사용자 응답 대기).
  - `implementation_candidate` → SKIPPED (approval gate 가 owner).
  `session.extra["discussion_followup"]["by_turn"][turn_id]` 마커가
  같은 (turn, role, kind) 재발사를 차단. 32 turn cap.
- `decision_port` seam 으로 외부 판단 layer 가 "이 turn 은 이미
  정착됨" 을 답하면 short-circuit (raise → deterministic fallback).

### 16-ter.3 completion funnel

- `agents/job_queue/completion_funnel.py::funnel_completion` —
  `record_completion` 의 4-state 에 producer tick 트리거를 묶음.
  - `done` / `retry_ready` → `producer.tick()` 호출.
  - `needs_approval` / `blocked` → tick 안 함, reason 만 기록.
  - tick 도중 raise → `ticked=False` + reason 만 기록. 재호출 안 함.
- `session.extra["completion_funnel"]["history"]` 32 entry capped audit.

### 16-ter.4 conflict guard / parallel-safe orchestration

- 읽기/분석/수집 계열은 producer 가 sub-producer 별 try/except 로 감싸 동시
  실행 가능.
- 같은 branch / session / coding_job 에 대해서는
  `AutonomyLockRegistry` 가 advisory lock 으로 직렬화. 두 번째 tick 은
  `LOCKED` outcome 으로 surface 하고 다음 tick 에 다시 시도.
- 큐 자체 dedup (`CodingExecutorWorker.find_active`,
  `RoleTakeWorker.find_active`, `ResearchWorker.find_active`) 이 hard
  correctness 를, lock 은 wasted work / log noise 감소를 담당.

### 16-ter.5 short-lived Claude invocation seam

- `agents/job_queue/claude_decision_seam.py` — `ClaudeDecisionPort`
  Protocol + `DeterministicDecisionPort` 기본 구현 + `compose_decision_port`
  composer. 이 PR 에서는 deterministic 만 land — live provider 는 별도
  PR + 운영자 승인.
- discussion follow-up 이 첫 소비처. retry guard / next task 도 동일
  vocabulary (`DECISION_KIND_*`) 로 확장 가능.
- 미설정 / raise / non-actionable 응답은 자동으로 deterministic fallback —
  callsite 코드 변경 없이 회귀.

### 16-ter.6 env contract + hard rails

- `YULE_AUTONOMY_PRODUCER_ENABLED=true` — supervisor 가 autonomy
  producer tick 을 실제로 구동할지 결정. 기본값은 opt-in 비활성. 미설정
  시 supervisor 는 sweep / status post 만 돈다.
- `YULE_AUTONOMY_PRODUCER_INTERVAL_SECONDS` — tick 간 최소 간격
  (기본 30 s, 하한 5 s). status post / self improvement gate 와 같은
  단조 시계 (`time_fn`) 위에서 동작.
- live LLM editor / decision provider 는 여전히 별도 PR. producer 는
  protected branch / force push 가드를 절대 건드리지 않고 worker hard
  rail (Round 3 16-bis.6) 그대로 신뢰.
- producer 가 큐 직접 enqueue 를 하지 않는다는 원칙 — 큐 dedup 한 곳
  유지. 새 sub-producer 는 반드시 기존 dispatcher 한 개를 거쳐야 한다.

## 16-quater. Live-ready Claude subprocess seam (Round 4-ter)

Round 4-bis 가 deterministic / record-only / live-ready 3-tier 의 *형식* 을 land 하면서도 라이브 callable 자체는 None 으로 두었다. Round 4-ter 는 그 callable 자리에 실제 `claude -p` 서브프로세스 호출을 꽂고, 모든 콜사이트가 같은 헬퍼로 진입하도록 정리한다.

### 16-quater.1 ``claude -p`` 어댑터

- `agents/job_queue/claude_subprocess_adapter.py` — `ClaudeSubprocessConfig`,
  `build_claude_subprocess_callable`, `claude_subprocess_factory_from_env`.
  내부에서 라이브 HTTP/SDK import 0; `subprocess.run` 만 호출. 모든 실패
  모드 (binary missing / timeout / non-zero exit / empty stdout / malformed
  JSON / unsupported payload / runner raise) 가 *non-actionable* 응답으로
  surface — 컴포저가 deterministic 으로 fall-through.
- 응답 metadata 에는 `provider=claude_subprocess` + 안정된
  `subprocess_outcome` 문자열을 박아 record-only / 운영자 대시보드가
  "왜 fall-back 했는지" 를 한 줄로 grep 할 수 있게 만듦.
- CLI 응답에 chatter 가 섞여 있어도 첫 `{...}` 블록을 잘라 파싱 — tip-of-day
  / update notice 가 묻혀 있어도 결정만 통과.

### 16-quater.2 두 단계 env opt-in

- `YULE_CLAUDE_DECISION_PROVIDER=external,deterministic` 만 켜도 라이브 tier
  는 surface 되지 않는다. `YULE_CLAUDE_DECISION_LIVE_ENABLED=true` 까지
  켜야 어댑터의 callable 이 factory 에서 반환된다.
- 두 키 어느 한 쪽만 설정해도 supervisor 의 trace 라인은
  `live=off` 또는 `external (no callable factory or factory returned None)`
  로 분명히 보고된다.
- 어댑터는 추가로 binary 가 PATH 에 없으면 callable 을 surface 하지 않는다
  — 운영자 typo 가 실 shell 호출로 새지 않는 3 중 가드.

### 16-quater.3 콜사이트 통일 + 호출별 감사 트레이스

- `claude_decision_seam.consult_decision_port` 헬퍼가 모든 콜사이트의
  진입점이 된다. 입력은 (port, request), 출력은
  `(DecisionResponse, DecisionInvocationTrace)`. None / raise / wrong-type
  세 경우 모두 동일하게 non-actionable + trace.fell_through / trace.raised
  surface — 콜사이트가 더 이상 try/except 를 직접 적지 않는다.
- 감사 트레이스(`DecisionInvocationTrace`) 는 JSON-safe 한 평면 dict 로
  직렬화 가능. autonomy producer 의 retry-guard 콜과 discussion follow-up
  의 SKIPPED outcome 양쪽 모두 dispatch payload 의 `decision_invocation`
  키에 trace 를 적재한다 — 운영자가 dashboard 에서 "이 skip 은 어떤
  provider 가 결정했고 actionable 이었나" 를 쿼리 한 번으로 확인.
- 새 `DECISION_KIND_IMPLEMENTATION_CANDIDATE` 토큰을 vocabulary 에 추가
  해 후속 PR 에서 candidate gate 콜이 같은 헬퍼/트레이스 경로로 진입할
  자리를 확보.

### 16-quater.4 운영 surface

- supervisor 부팅 시 한 줄: `claude decision port composed: enabled=...
  fallback=deterministic skipped=... live=on/off`. 운영자가 라이브가
  실제 켜졌는지 grep 한 번으로 확인.
- 끄려면 `YULE_CLAUDE_DECISION_LIVE_ENABLED` 만 비우면 됨 — provider chain
  은 그대로여도 어댑터가 None 반환 + trace 라인이 `live=off` 로 기록.
- record-only 와 동시 활성 권장 ramp: `external,record,deterministic` +
  `YULE_CLAUDE_DECISION_RECORD_PATH=/var/log/yule/decision-shadow.jsonl` —
  external actionable 이면 그 verdict, 아니면 record 가 캡처 후
  deterministic.

## 17. 최종 판단

지금의 1순위는 `tech-lead` 완성이다.

- multi-bot 확장은 후순위
- CI는 이미 1차 기반이므로 유지
- CD는 로컬 완결 루프 후 검토
- discussion + research + execution + retry를 한 흐름으로 묶는 것이 핵심

이 문서를 기준으로 앞으로의 구현 판단을 고정한다.
