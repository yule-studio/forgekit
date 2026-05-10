---
title: "tech-lead runtime loop — 작업 로그"
kind: task-log
issue: 73
parent_issue: 20
session_id: issue-73-tech-lead-runtime
project: yule-studio-agent
created_at: 2026-05-09T00:00:00+09:00
status: in-progress
branch: feature/tech-lead-runtime-loop
worktree: /Users/masterway/local-dev/yule-studio-agent-worktrees/issue-73-tech-lead-runtime
mode: tech-lead-mediated (다역할 통합 결정 layer)
tags: [task-log, tech-lead-runtime, foundation]
---

# 목표

4 단계 (coding executor / completion hook + selector / decision layer / 검증) 를 단일 PR foundation 까지 land. 자세한 결정은 [[2026-05-09_issue-73-decision-tech-lead-runtime-loop]], 분석은 [[2026-05-09_issue-73-research-tech-lead-runtime-loop]].

# 현재 Yule 기준선

11 state machine + 4 worker (research / role / approval / obsidian) + service registry (engineering profile 12 spec) + autonomy_policy L0~L4 + governance 4 layer (#69). `coding_job` 데이터 모델 존재하나 executor 가 없어 `STATUS_READY` 가 dead-end.

# 진행 단계 (실시간 갱신)

| 시점 | 단계 | 상세 |
| --- | --- | --- |
| 2026-05-09 kickoff | sub-issue + worktree | issue #73 (parent #20). branch `feature/tech-lead-runtime-loop`. |
| 2026-05-09 commit-1 | Obsidian 노트 3 종 | 본 노트 + research + decision land. |
| 2026-05-09 commit-2 | Phase 1 — coding_executor_worker scaffold + service spec + tests | 완료 |
| 2026-05-09 commit-3 | Phase 2 — completion_hook + next_task_selector + tests | 완료 |
| 2026-05-09 commit-4 | Phase 3 — decision/router + context_pack + tests | 완료 |
| 2026-05-09 commit-5 | Phase 4 — runtime services 통합 + 회귀 보호 | 완료 |
| 2026-05-09 round-2 kickoff | live wiring 4종 → 같은 PR / 같은 branch 위에서 추가 commit. | |
| 2026-05-09 commit-6 | Round 2 — A. live executor wiring (`coding_executor_live.py` 590 + tests 380) | 완료, Protocol 5/6 활성, LLM editor 만 blocker |
| 2026-05-09 commit-7 | Round 2 — B. real classifier wiring (`classifier_factory.py`, OllamaClassifier live + Anthropic/OpenAI adapter contract) | 완료, env 2-tier 인증 |
| 2026-05-09 commit-8 | Round 2 — C. auto-spawn opt-in (`YULE_CODING_EXECUTOR_AUTOSPAWN` env flag) | 완료, runtime up 연결 |
| 2026-05-09 commit-9 | Round 2 — D. CI failure → retry loop (`ci_status.py` + selector 가드) | 완료, 무한 재시도 차단 |
| 2026-05-09 commit-10 | Round 2 — task-log + governance 회귀 + PR body 갱신 | 본 commit |
| 2026-05-09 round-3 kickoff | live wiring 4 종 (dispatcher / 서비스 spawn / CI orchestrator / progress hook) — 같은 PR / 같은 branch | |
| 2026-05-09 commit-11 | Round 3 — A. coding_execute_dispatcher (`coding_execute_dispatcher.py` + tests) | 완료 |
| 2026-05-09 commit-12 | Round 3 — B. eng-coding-executor 서비스 spawn + LiveGithubAppClient.list_check_runs / get_pull_request | 완료 |
| 2026-05-09 commit-13 | Round 3 — C. CI retry orchestrator + Obsidian/GitHub progress hook | 완료 |
| 2026-05-09 commit-14 | Round 3 — D. 마스터 플랜 16-bis 섹션 + 본 task-log Round 3 갱신 | 본 commit |
| 2026-05-09 round-4 kickoff | autonomy producer / scheduler — 별도 worktree `feature/company-runtime-autonomy-loop` 위에서 commit 15~17 | |
| 2026-05-09 commit-15 | Round 4 — A. autonomy_producer + autonomy_lock + tests | 완료, producer/scheduler core land |
| 2026-05-09 commit-16 | Round 4 — B. discussion_followup + completion_funnel + tests | 완료, discussion → 큐 / completion → tick funnel |
| 2026-05-09 commit-17 | Round 4 — C. claude_decision_seam + supervisor watch loop tick + 마스터 플랜 16-ter | 완료, runtime 자율 tick + 외부 결정 layer seam |
| 2026-05-10 round-4-bis kickoff | decision provider 강화 — 별도 worktree `feature/company-runtime-autonomy-decision` 위에서 commit 18~19 | |
| 2026-05-10 commit-18 | Round 4-bis — A. RecordOnly/External port + env contract factory + DecisionRequest 정착 | 완료, deterministic / record-only / live-ready 3-tier 명확화 |
| 2026-05-10 commit-19 | Round 4-bis — B. autonomy producer retry-guard 호출 경로 + run_service env-driven 합성 + 본 task-log 갱신 | 완료, autonomy loop 가 실제 decision port 를 호출하는 첫 경로 |
| 2026-05-10 commit-20 | Round 4-ter — A. claude -p subprocess adapter (live-ready callable) + 어댑터 테스트 + run_service factory 연결 | 완료, 외부 callable 자리에 실제 ``claude -p`` 호출 경로 land — env 두 단계 opt-in 미설정 시 행동 변화 0 |
| 2026-05-10 commit-21 | Round 4-ter — B. consult_decision_port + DecisionInvocationTrace + autonomy/discussion 콜사이트 통일 + 라이브 트레이스 audit + task-log/마스터 플랜 갱신 | 완료, decision seam 의 호출/감사 경로가 한 곳에 정착 |

# 변경 / 산출물 (계획)

| 영역 | 위치 |
| --- | --- |
| Phase 1 worker | `src/yule_orchestrator/agents/job_queue/coding_executor_worker.py` |
| Phase 2 hook + selector | `src/yule_orchestrator/agents/job_queue/{completion_hook,next_task_selector}.py` |
| Phase 3 decision | `src/yule_orchestrator/agents/decision/{__init__,router,context_pack}.py` |
| Phase 4 services | `src/yule_orchestrator/runtime/services.py` (+1 spec) |
| Tests | `tests/agents/test_coding_executor_worker.py`, `test_completion_hook.py`, `test_next_task_selector.py`, `test_decision_router.py`, `test_context_pack.py` |
| Obsidian mirror | `notes/vault-mirror/10-projects/yule-studio-agent/{research,decisions,task-logs}/2026-05-09_issue-73-*.md` |

# 도입 / 보류 / 비도입

[[2026-05-09_issue-73-decision-tech-lead-runtime-loop]] 의 12 결정 (D-73-1 ~ D-73-12) 그대로.

후속 PR 분리: live executor 호출 / runtime up auto-spawn / Discord blocked 통지 / 실 GitHub state query / 실 Claude classifier / autonomy_policy hook.

# 왜 회사형 시니어 팀 운영에 필요한가

작업 종료 후 *다음 작업 선택* 이 사람 input 없이 deterministic 하게 일어난다. 부서가 "다음 뭐해?" 를 스스로 답한다. 외부 blocker 만 사람을 부른다.

# 리스크 + 다음 액션

리스크:

- 본 PR 의 worker / selector / classifier 는 *모두 fake injection* 까지만. 실 wiring 은 후속 PR 의 사용자 승인 + secret 확인 필요.
- 새 service kind 가 spec 에 등록되지만 `runtime up` 의 자동 spawn 은 *opt-out* — 사용자가 활성화 결정.
- `coding_execute` worker 가 protected branch / force push 를 worker 차원에서 차단 — 정책 위반 시 worker fail.

다음 액션 (본 PR):

1. Phase 1 commit (coding_executor_worker + service kind enum)
2. Phase 2 commit (completion_hook + next_task_selector)
3. Phase 3 commit (decision router + context_pack)
4. Phase 4 commit (services registry + 회귀 보호 test)
5. push + draft PR + progress comment

# 갱신 (커밋 단계 종료 후)

- commit hash 5 종 + 각 commit 목적
- unit 테스트 결과
- draft PR URL
- 본 PR 비범위 항목별 후속 PR 매핑

# Round 2 — 종료 시점 갱신 (2026-05-09)

## 결과 요약

같은 PR / 같은 branch / 같은 worktree 위에서 commit 6~10 추가. Round 1 의 protocol-only foundation 위에 4 영역 live wiring 을 한 번에 land.

## 산출물 (Round 2)

| 영역 | 위치 | 비고 |
| --- | --- | --- |
| A. live executor | `src/yule_orchestrator/agents/job_queue/coding_executor_live.py` | 590 라인, Protocol 5/6 구현 (worktree / record-only editor / subprocess test runner / git committer / GithubAppPusher / GithubAppDraftPRCreator) + factory + availability summary. LLM 코드 편집기는 blocker 로 명시. |
| A. tests | `tests/agents/test_coding_executor_live.py` | 16 케이스, 실제 git 임시 repo + fake LiveGithubAppClient. |
| B. classifier factory | `src/yule_orchestrator/agents/decision/classifier_factory.py` | 432 라인. OllamaClassifier(live) + Anthropic / OpenAI 어댑터 컨트랙트(blocked stub). `build_classifier_from_env()` 우선순위(anthropic > openai > ollama) + 2단계 인증(키/엔드포인트 + `YULE_DECISION_<provider>_ENABLED=true`). |
| B. tests | `tests/agents/test_classifier_factory.py` | 37 케이스. JSON 파서 (직접/embedded/malformed/unknown mode/confidence clamp), Ollama HTTP 실패 fallback, blocked adapter, env priority, API key 누출 가드. |
| C. auto-spawn opt-in | `src/yule_orchestrator/runtime/services.py` | `YULE_CODING_EXECUTOR_AUTOSPAWN` env flag. `_build_engineering_profile(env)` + `is_coding_executor_autospawn_enabled` 헬퍼 public. |
| C. .env.example | `.env.example` | autospawn 플래그 + decision classifier env 가이드 (false 기본 + 운영 정책 명시). |
| C. tests | `tests/runtime/test_services.py` (+6), `tests/runtime/test_subprocess_supervisor.py` (+1) | env truthy/falsey 매트릭스, 다른 spec 무영향, GitHub App env 만으로는 활성화 안 됨, 모듈 reload 후 dry-run plan 반영. |
| D. CI retry loop | `src/yule_orchestrator/agents/job_queue/ci_status.py` | 350 라인. CIStatus + from_check_runs 집계, CIRetryPolicy(default 3 attempts, ×2 backoff, cap 30 min), RetryAttemptLog/RetryVerdict, decide_retry, derive_completion_status_from_ci, partition_failed_prs_by_retry. |
| D. selector 통합 | `src/yule_orchestrator/agents/job_queue/next_task_selector.py` | `select_next_task_with_ci_retry_guard` 신규. escalated PR 은 candidate.payload[ci_retry_escalated] surface. |
| D. tests | `tests/agents/test_ci_status.py` | 35 케이스. check run 집계 (success/failure/cancelled/timed_out/pending/unknown), backoff 계산 + cap, decide_retry 7가지 분기, log 영속성, partition + selector 통합. |

## 회귀 검증

- `python3 -m unittest discover -s tests -t .` → **2992/2992 OK** (skip 5).
- 신규 테스트만 138 케이스 (Round 1 기준 +106).

## Hard rails 보존 확인

- 보호 브랜치(`is_protected_branch`) push 차단: Phase 1 그대로 유지.
- LLM 코드 편집기: `RecordOnlyCodeEditor` 가 plan markdown 만 작성, source 변경 없음.
- 외부 LLM provider: Anthropic/OpenAI 어댑터는 blocked stub. live 호출은 후속 PR 운영자 승인 후.
- 자동 spawn: 명시 env flag(`YULE_CODING_EXECUTOR_AUTOSPAWN=true`) 없으면 비활성. 다른 env(키 등)로는 활성화 불가.
- 무한 재시도: max_attempts(default 3) 도달 시 `blocked` 로 escalate. policy.max_attempts=0 misconfig 도 즉시 escalate.
- API key 누출: 어댑터 reason / payload / log 어디에도 노출되지 않음 (테스트로 가드).

## 본 PR 비범위 → 후속 PR 매핑 (Round 2 갱신)

- LLM 코드 편집기 활성화 → 별도 PR. operator 승인 + cost 검토.
- Anthropic / OpenAI live 호출 → 별도 PR. cost-budget 검토 + secret 관리.
- workflow_state 와 ci_status 의 실 wiring (PR head_sha 폴링, retry log 영속) → 별도 PR. G6 LiveGithubAppClient 의 check-run 조회 RPC 추가 필요.
- Discord notification (escalated PR / blocked job operator alert) → 별도 PR.

## 외부 blocker

- 없음. Round 2 4영역 모두 hard-rail 안에서 land. 추가 진행은 운영자 승인 게이트가 필요한 후속 PR 들로 분기.

## 관련 문서

- [[CLAUDE]]
- [[2026-05-09_issue-73-research-tech-lead-runtime-loop]]
- [[2026-05-09_issue-73-decision-tech-lead-runtime-loop]]
- [[2026-05-08_issue-69-research-engineering-agent-governance-synthesis]]
- [[2026-05-08_issue-69-decision-engineering-agent-authoring-policy]]

# Round 3 — 종료 시점 갱신 (2026-05-09)

## 결과 요약

같은 PR / 같은 branch 위에서 commit 11~14 추가. Round 1 의 protocol foundation, Round 2 의 live executor + CI policy 위에 producer / 서비스 spawn / orchestrator / progress hook 4 영역을 한 번에 land — 회사형 runtime 의 execution + improvement 루프가 처음으로 end-to-end deterministic.

## 산출물 (Round 3)

| 영역 | 위치 | 비고 |
| --- | --- | --- |
| A. coding_execute dispatcher | `src/yule_orchestrator/agents/job_queue/coding_execute_dispatcher.py` (480 라인) | iter_ready_coding_jobs / build_coding_execute_request / dispatch_ready_coding_jobs / WorkflowSessionState. session.extra["coding_execute_dispatch"] marker + worker.find_active 양쪽으로 dedup. |
| A. tests | `tests/job_queue/test_coding_execute_dispatcher.py` (23 케이스) | iter 필터 / env 우선순위 / 큐 wiring / idempotency / loader 실패 가드. |
| B. 서비스 spawn | `src/yule_orchestrator/runtime/run_service.py` | _build_process_job 의 CODING_EXECUTOR 분기 + build_coding_executor_bundle + dispatcher 틱 + progress recorder hook. |
| B. CI 조회 endpoint | `src/yule_orchestrator/github_app/live_client.py` | list_check_runs / get_pull_request 추가, /repos/{repo}/commits/{sha}/check-runs 결과를 ci_status.from_check_runs 와 직접 호환되는 dict 로 투영. |
| B. tests | `tests/runtime/test_run_service_coding_executor.py` (8 케이스), `tests/github_app/test_live_client_check_runs.py` (5 케이스) | env matrix / factory 실패 / 클로저 wiring / dispatcher 틱 / GET endpoint round-trip. |
| C. CI retry orchestrator | `src/yule_orchestrator/agents/job_queue/ci_retry_orchestrator.py` (480 라인) | orchestrate_ci_retry / GithubAppCheckRunFetcher / CIRetryDecision. retry 시 branch_hint 에 -attemptN suffix 붙여 새 행 enqueue, terminal 시 completion_hook funnel. |
| C. progress hook | `src/yule_orchestrator/agents/job_queue/coding_execute_progress.py` (430 라인) | record_coding_execute_progress / make_github_pr_comment_fn. task-log obsidian_write 큐 + 선택적 PR comment + 50 행 capped history. |
| C. tests | `tests/job_queue/test_ci_retry_orchestrator.py` (9 케이스), `tests/job_queue/test_coding_execute_progress.py` (11 케이스) | success / under-budget / over-budget / unknown / pending / GitHub failure / dry-run skip / collaborator 부재. |

## 회귀 검증

- `python3 -m unittest discover -s tests -t .` → **3117/3117 OK** (skip 5).
- 신규 테스트만 56 케이스 (Round 1+2 기준 +56).

## Hard rails 보존 확인 (Round 3)

- 보호 브랜치 push 차단: 그대로.
- LLM 코드 편집기 비활성: 그대로 (RecordOnlyCodeEditor).
- live GitHub push: GitHub App env 3 종 모두 갖춰진 경우만, 부분 설정으로는 절대 활성화 안 됨.
- 무한 재시도: decide_retry max_attempts 그대로, orchestrator 는 verdict 신뢰.
- task-log 노트만 자동 enqueue (knowledge / decision-record 같은 approval-required kind 는 제외).
- progress poster 실패 swallowed — verdict 무결성 보존.

## 본 PR 비범위 → 후속 PR 매핑 (Round 3 갱신)

- live LLM 코드 편집기 활성화 → 별도 PR. operator 승인 + cost 검토.
- CI orchestrator 의 supervisor watch loop 자동 호출 (현재는 명시적 호출 / 테스트 inject 까지만 — 후속 PR 에서 지정 인터벌 polling).
- Discord 봇 alert (escalated PR / blocked job 운영자 알림) → 별도 PR.

## 외부 blocker

- 없음. Round 3 4 영역 모두 hard-rail 안에서 land. live LLM editor 만 명시적 별도 PR.


# Round 4 — 종료 시점 갱신 (2026-05-09)

## 결과 요약

별도 worktree `feature/company-runtime-autonomy-loop` 위에서 commit 15~17 추가. Round 3 의 dispatcher / orchestrator / progress hook 위에 producer 계층 / discussion 자동 follow-up / completion funnel / 외부 결정 seam 까지 land — 사람이 메시지를 안 넣어도 runtime 이 작업을 이어가는 그림이 처음으로 닫힌다.

## 산출물 (Round 4)

| 영역 | 위치 | 비고 |
| --- | --- | --- |
| A. autonomy producer / scheduler | `src/yule_orchestrator/agents/job_queue/autonomy_producer.py` (480 라인) | AutonomyProducer.tick() = selector poll + 승인 coding_job + unresolved discussion + CI failure funnel. AutonomyProducerReport / AutonomyDispatch / DispatchOutcome. 큐 직접 enqueue 금지, 기존 dispatcher 위에 얇게 얹음. |
| A. lock registry | `src/yule_orchestrator/agents/job_queue/autonomy_lock.py` (170 라인) | AutonomyLockRegistry — branch / session / coding_job 스코프별 단명 advisory lock, in-memory + TTL + lazy reclaim, thread-safe. |
| A. tests | `tests/job_queue/test_autonomy_producer.py` (7 케이스), `tests/job_queue/test_autonomy_lock.py` (11 케이스) | selector idle / 승인 coding_job dispatch / 두 tick idempotency / pre-locked 스코프 / 동시성 winner 1. |
| B. discussion follow-up | `src/yule_orchestrator/agents/job_queue/discussion_followup.py` (520 라인) | 4 mode 분기 (discussion+missing_roles → role_take, research_only → research_collect, clarification/implementation → SKIPPED). session.extra["discussion_followup"] turn-bucket 마커 32 cap. decision_port seam. |
| B. completion funnel | `src/yule_orchestrator/agents/job_queue/completion_funnel.py` (250 라인) | record_completion + producer tick 트리거. done/retry_ready 만 tick, needs_approval/blocked 는 deferred. session.extra["completion_funnel"]["history"] 32 cap. |
| B. tests | `tests/job_queue/test_discussion_followup.py` (11 케이스), `tests/job_queue/test_completion_funnel.py` (8 케이스) | 4 mode 라우팅 / 마커 32-trim / decision_port skip + raise / 4-state routing / tick raise → 격리 / closure factory. |
| C. Claude decision seam | `src/yule_orchestrator/agents/job_queue/claude_decision_seam.py` (190 라인) | ClaudeDecisionPort Protocol + DeterministicDecisionPort + compose_decision_port. live provider 는 별도 PR. raise / non-actionable → fallback. |
| C. supervisor 자율 tick | `src/yule_orchestrator/agents/job_queue/worker_loop.py`, `src/yule_orchestrator/runtime/run_service.py` | run_supervisor_watch_loop 에 autonomy_producer_tick_fn / interval / on_report 인자 추가. _build_autonomy_producer_tick 이 producer + 모든 worker + 디스패처를 process 안에서 한 인스턴스씩 공유. ENV_AUTONOMY_PRODUCER_ENABLED=true 가 활성 스위치 (기본 dormant). |
| C. tests | `tests/job_queue/test_claude_decision_seam.py` (10 케이스), `tests/runtime/test_supervisor_autonomy_tick.py` (5 케이스) | port priority / fallback / Protocol duck-type / interval gate / dormant 모드 / tick raise 격리 / on_report 후크 / env 미설정 시 dormant. |
| C. 마스터 플랜 16-ter | `docs/engineering-company-runtime-master-plan.md` | producer / discussion jobization / completion funnel / conflict guard / Claude seam / env contract 6 소절 신설. |

## 회귀 검증

- `tests.job_queue` 287 cases / `tests.runtime` 278 cases / `tests.discord` 335 cases — 전부 통과.
- 신규 테스트 52 케이스 (autonomy_lock 11 + autonomy_producer 7 + discussion_followup 11 + completion_funnel 8 + claude_decision_seam 10 + supervisor_autonomy_tick 5).
- 전체 `tests.discover` 3166 cases 중 1 건 (test_login_failure_translates_to_value_error) 만 pre-existing test 상호 오염 — isolation 시 통과, 본 PR 무관.

## Hard rails 보존 확인 (Round 4)

- 큐 dedup 한 곳 원칙: producer 는 큐 직접 enqueue 안 함, 모든 enqueue 는 기존 dispatcher 한 곳을 거친다.
- protected branch / force push: Round 3 가드 그대로, producer 가 건드리지 않음.
- live LLM editor / decision provider: 여전히 별도 PR — 본 PR 의 DeterministicDecisionPort 가 default 라 모든 callsite 가 fallback.
- supervisor 자율 tick 활성화: 명시 env flag (`YULE_AUTONOMY_PRODUCER_ENABLED=true`) 미설정 시 supervisor 행동 변화 없음.
- 동시성: AutonomyLockRegistry 는 advisory; 두 번째 tick 은 LOCKED 로 surface + 다음 tick 재시도. hard correctness 는 큐 dedup.

## 사람 입력 없이 runtime 이 다음 작업을 어떻게 만드는가

1. 어떤 worker (research / role / approval / obsidian / coding_executor) 가 종료하면 본 PR 의 `completion_funnel.funnel_completion` 이 호출된다.
2. funnel 은 4-state 결정 (`done` / `retry_ready` 면 producer tick, `needs_approval` / `blocked` 이면 미tick) 을 한다.
3. producer tick 이 실행되면 `AutonomyProducer.tick()` 이 selector + 3 개 sub-producer 를 돈다:
   - 승인 coding_job 이 있으면 `coding_execute_dispatcher.dispatch_ready_coding_jobs` 로 새 coding_execute 행 enqueue.
   - unresolved discussion 이 있으면 `discussion_followup` 이 모드별로 role_take / research_collect 를 enqueue.
   - failed CI PR 이 있으면 `completion_dispatch` 인자가 있는 한 funnel 로 라우팅 (CI retry orchestrator 가 owner).
4. supervisor watch loop 도 `autonomy_producer_tick_fn` interval 마다 같은 tick 을 호출 — completion 이 안 떨어진 idle 시간에도 새 작업이 발견되면 enqueue.
5. 모든 enqueue 는 (a) 큐 dedup, (b) session.extra 마커, (c) AutonomyLockRegistry 3중 가드로 폭주 차단.

결과: Round 3 까지는 사람이 "수정 승인" 을 입력해야 다음 단계가 시작됐다면, Round 4 부터는 디스코드 토의가 끊긴 상태에서도 supervisor 가 매 30 초 tick 으로 (a) 승인 대기 중인 coding_job 이 있는지, (b) 토의 후속 role_take 가 비어 있는지, (c) failed CI PR 이 있는지 스스로 확인하고 큐를 채운다.

## 본 PR 비범위 → 후속 PR 매핑 (Round 4 갱신)

- live Claude / external decision provider 활성화 → 별도 PR. compose_decision_port 위에 live port 를 얹기만 하면 됨.
- live LLM 코드 편집기 활성화 → 별도 PR (Round 2 부터 동일 매핑).
- Discord 봇 alert (escalated PR / blocked job 운영자 알림) → 별도 PR.
- 역할별 자료 수집 background ingestion live wiring (Phase 5) → 별도 worktree.

## 외부 blocker

- 없음. Round 4 3 영역 모두 hard-rail 안에서 land. supervisor 자율 tick 도 opt-in env 로 운영자 승인 게이트 유지.

# Round 4-bis — 종료 시점 갱신 (2026-05-10)

## 결과 요약

별도 worktree `feature/company-runtime-autonomy-decision` 위에서 commit 18~19 추가. Round 4 가 land 한 `claude_decision_seam.py` 의 단일 `DeterministicDecisionPort` 위에 (a) `RecordOnlyDecisionPort` (shadow-mode 캡처) + (b) `ExternalDecisionPort` (외부 호출 가능 객체 어댑터) + (c) env-driven `build_decision_port_from_env` 합성기 를 얹어 — 짧은 Claude invocation seam 이 deterministic / record-only / live-ready 3 tier 로 명확하게 갈라졌다. 동시에 autonomy producer 의 CI retry 서브-producer 가 실제로 decision port 를 호출하는 첫 경로를 land.

## 산출물 (Round 4-bis)

| 영역 | 위치 | 비고 |
| --- | --- | --- |
| A. seam 강화 | `src/yule_orchestrator/agents/job_queue/claude_decision_seam.py` | RecordOnlyDecisionPort (ring buffer + JSONL append, 비-actionable so chain falls through) / ExternalDecisionPort (외부 callable 어댑터, raise → fallback / Mapping → DecisionResponse 정규화) / build_decision_port_from_env (env contract: `YULE_CLAUDE_DECISION_PROVIDER` 우선순위 토큰화 + record path / buffer / external timeout) / DecisionPortBuildTrace / coerce_decision_request 헬퍼. 라이브 HTTP 클라이언트 import 절대 없음 — 외부 callable 은 `external_callable_factory` 로 주입. |
| A. discussion follow-up DecisionRequest 정착 | `src/yule_orchestrator/agents/job_queue/discussion_followup.py` | `_build_decision_request` 가 typed `DecisionRequest` 를 반환 (기존: 느슨한 dict). external port 가 `request.kind` / `request.facts` 의존 가능. |
| A. tests | `tests/job_queue/test_claude_decision_seam.py` (+19 cases), `tests/job_queue/test_discussion_followup.py` (1 stub 검증 갱신) | RecordOnly ring buffer / JSONL append / chain fall-through, ExternalDecisionPort 6 path (no callable / response passthrough / mapping normalise / no-timeout signature / raise → fallback / unsupported return), env factory 9 path (default / record / external skip / external active / unknown token / record path / buffer clamp / factory raise / trace shape), coerce_decision_request 4 path. |
| B. autonomy producer retry-guard | `src/yule_orchestrator/agents/job_queue/autonomy_producer.py` | `_produce_ci_retry_followup` 가 dispatcher 호출 *전에* `decision_port.decide(kind=retry_guard, facts={pr_number, attempt, escalated})` 를 호출. skip → SKIPPED dispatch + branch lock 미획득 (port 가 위임을 거부했으면 lock 도 안 잡음). raise / non-actionable → 기존 fast-path. |
| B. run_service env 합성 | `src/yule_orchestrator/runtime/run_service.py` | `_build_autonomy_producer_tick` 가 `build_decision_port_from_env(external_callable_factory=_resolve_external_decision_callable_factory())` 호출 후 `AutonomyProducer(decision_port=...)` 로 주입. `_resolve_external_decision_callable_factory` 는 본 PR 에서 `None` 반환 (= deterministic-only) — 후속 PR 에서 monkeypatch 한 줄로 라이브 callable 활성화. `_log_decision_port_trace` 가 supervisor 시작 시 enabled / fallback / skipped 토큰 한 줄 로그. |
| B. tests | `tests/job_queue/test_autonomy_producer.py` (+4 cases), `tests/job_queue/test_decision_port_run_service_wiring.py` (3 cases) | retry-guard skip 시 dispatcher 미호출 / advance 시 dispatcher 호출 / raise 시 fast-path / port 미연결 시 legacy / run_service env 미설정 → deterministic-only / external factory monkeypatch → live skip / record token → record-only 합성. |

## 회귀 검증

- `python3 -m unittest discover -s tests -t . -p 'test_*.py'` → **3196/3196 OK** (skip 5).
- 신규 테스트 26 케이스 (seam +19 / autonomy +4 / wiring +3).
- Round 4 의 supervisor autonomy tick 회귀 (`tests.runtime.test_supervisor_autonomy_tick`) 6 cases 그대로 통과.

## Hard rails 보존 확인 (Round 4-bis)

- 라이브 HTTP / API 클라이언트 import 절대 없음 — `ExternalDecisionPort` 는 외부 callable 어댑터일 뿐.
- 운영자가 `YULE_CLAUDE_DECISION_PROVIDER=external,deterministic` 을 켜도 callable factory 가 `None` 을 반환하면 external tier 는 skipped 로 trace 에 기록되고 deterministic 만 유효 — 명시적 라이브 callable 주입 없이는 행동 변화 0.
- record-only 는 *비-actionable* — chain 의 verdict 를 절대 가로채지 않음. shadow mode 정의 그대로.
- decision port 가 raise 하거나 잘못된 타입을 반환하면 모든 callsite 가 fast-path 로 떨어짐 — runtime 정지 불가.
- 큐 dedup / branch lock / session marker 3 중 가드는 Round 4 그대로 유지.

## autonomy loop 가 외부 결정 layer 를 실제로 어떻게 부르는가

1. supervisor 가 `YULE_AUTONOMY_PRODUCER_ENABLED=true` 로 부팅하면 `_build_autonomy_producer_tick` 이 env 기반 decision port 를 합성한다 (`YULE_CLAUDE_DECISION_PROVIDER` 미설정 → deterministic-only).
2. 매 30 s tick 마다 `AutonomyProducer.tick()` 이 4 sub-producer 를 돈다. CI failed PR 후보가 잡히면 `_consult_retry_guard` 가 `DecisionRequest(kind=retry_guard, facts={pr_number, attempt, escalated})` 로 port 를 호출.
3. port chain: `external (있으면) → record (있으면) → deterministic`. external 이 skip 을 반환하면 producer 는 dispatcher 를 부르지 않고 SKIPPED dispatch 로 surface — 즉 라이브 결정 layer 가 *실제로* runtime 동작을 바꾸는 첫 경로.
4. record-only 는 verdict 를 가로채지 않으므로 운영자는 "라이브가 *무엇* 을 묻는지" 를 JSONL audit 으로 먼저 본 뒤 외부 callable 을 켜는 단계적 ramp 가 가능.
5. discussion follow-up 도 동일한 typed `DecisionRequest` 로 port 를 호출하므로 두 callsite 의 외부 prompt 템플릿이 일치.

## 본 PR 비범위 → 후속 PR 매핑 (Round 4-bis 갱신)

- 라이브 Claude API / 호스팅 결정 sidecar 의 실제 callable 구현 → 별도 PR. `_resolve_external_decision_callable_factory` 한 곳만 monkeypatch.
- next-task selector 단계의 decision port 호출 (DECISION_KIND_NEXT_TASK) → 별도 PR.
- record-only audit JSONL 의 회수 / 운영 대시보드 → 별도 PR (Discord 알림은 본 PR scope 밖).

## 외부 blocker

- 없음. 본 PR 도 hard-rail 안에서 land — deterministic-only 가 default 이므로 운영자가 명시 opt-in 하지 않는 한 supervisor 동작은 Round 4 와 동일.

# Round 4-ter — 종료 시점 갱신 (2026-05-10)

## 결과 요약

같은 worktree `feature/company-runtime-autonomy-decision` / 같은 PR(#78) 위에서 commit 20~21 추가. Round 4-bis 가 만들어 둔 `ExternalDecisionPort` 의 callable 자리에 (a) 실제 `claude -p` 를 호출하는 첫 어댑터, (b) 모든 콜사이트가 공유하는 `consult_decision_port` 헬퍼 + `DecisionInvocationTrace` 감사 트레이스, (c) `run_service` 의 두 단계 env opt-in 합성기 를 land. 결과: 운영자가 두 env 키를 모두 켰을 때만 supervisor 가 짧은 결정 호출을 실제 `claude` 서브프로세스로 시도하며, 그 외에는 deterministic-only 동작이 유지된다.

## 산출물 (Round 4-ter)

| 영역 | 위치 | 비고 |
| --- | --- | --- |
| A. live-ready 서브프로세스 어댑터 | `src/yule_orchestrator/agents/job_queue/claude_subprocess_adapter.py` | `ClaudeSubprocessConfig` / `build_claude_subprocess_callable` / `claude_subprocess_factory_from_env` / `render_subprocess_prompt`. 내부에서 라이브 HTTP/SDK import 절대 없음 — `subprocess.run` 만. timeout / 빈 stdout / non-zero exit / malformed JSON / unsupported payload / runner raise / binary missing 모두 `metadata['subprocess_outcome']` 에 stable 문자열로 surface 후 non-actionable 응답 → composer 가 deterministic 으로 fallthrough. CLI 응답 주위 chatter 가 있어도 첫 `{...}` 블록 파싱. |
| A. 어댑터 테스트 | `tests/job_queue/test_claude_subprocess_adapter.py` (22 케이스) | 프롬프트 round-trip / skip+advance 정상 / extra args + model 전달 / per-call timeout / chatter 파싱 / 7 가지 실패 모드 / env 두 단계 opt-in 4 path / 타임아웃 클램프 / 컴포저 통합 (`build_decision_port_from_env` + 라이브 토큰). |
| B. seam 콜 헬퍼 + 감사 트레이스 | `src/yule_orchestrator/agents/job_queue/claude_decision_seam.py` | `consult_decision_port(port, request) → (DecisionResponse, DecisionInvocationTrace)`. None / raise / wrong-type 모두 non-actionable + trace.fell_through / trace.raised 로 surface. `DecisionInvocationTrace` 는 JSON-safe 한 평면 dict 로 직렬화 가능 (`to_payload`). `DECISION_KIND_IMPLEMENTATION_CANDIDATE` 키 vocabulary 확장. |
| B. 콜사이트 통일 | `autonomy_producer.py::_consult_retry_guard`, `discussion_followup.py::_compute_outcomes` | 둘 다 `consult_decision_port` 로 일원화. retry-guard 는 `AutonomyDispatch.payload['decision_invocation']` 에 trace 적재. discussion follow-up 의 SKIPPED outcome 도 `payload['decision_invocation']` 에 trace 적재. |
| B. run_service 합성기 | `src/yule_orchestrator/runtime/run_service.py` | `_resolve_external_decision_callable_factory` 가 기본으로 `claude_subprocess_factory_from_env` 반환 (= 어댑터 자체가 env-gated 이므로 운영자가 opt-in 안 하면 None). `_log_decision_port_trace` 가 부팅 시 `live=on/off` 토큰을 trace 라인 끝에 출력 — 운영자가 한 줄로 "지금 진짜 claude 부를 거냐" 를 확인 가능. |
| B. 추가 테스트 | `tests/job_queue/test_claude_decision_seam.py` (+7 cases), `tests/job_queue/test_decision_port_run_service_wiring.py` (+3 cases), `tests/job_queue/test_autonomy_producer.py` (+1 audit assertion) | consult 헬퍼 7 path / run_service 라이브 어댑터 wiring 3 path (default factory 라이브 활성 / live flag 미설정 → None / 두 키 전부 설정 → 슈퍼바이저가 라이브 callable 보유) / retry-guard dispatch 의 audit payload. |

## 회귀 검증

- `python3 -m unittest discover -s tests -t . -p 'test_*.py'` → **3228/3228 OK** (skip 5).
- 신규 테스트 32 케이스 (subprocess +22 / consult helper +7 / run_service wiring +3).
- Round 4-bis 의 26 케이스 회귀 그대로 통과 — 기존 duck-typed `_StubAdvice` 한 곳만 typed `DecisionResponse` 로 변경 (콘트랙트 강화).

## Hard rails 보존 확인 (Round 4-ter)

- 라이브 HTTP / SDK import 0 — 어댑터는 `subprocess.run` 한 함수만 호출. 활성화하더라도 `claude` CLI 자체가 외부 인증을 수행한다.
- 두 단계 opt-in: `YULE_CLAUDE_DECISION_PROVIDER` 가 `external` 토큰을 포함하고 *동시에* `YULE_CLAUDE_DECISION_LIVE_ENABLED=true` 일 때만 라이브 callable 이 surface. 한 쪽만 켜져 있으면 trace 가 `external (no callable factory or factory returned None)` 로 skip 기록 + deterministic-only.
- 어댑터 자체도 binary 가 PATH 에 없으면 callable 을 surface 하지 않음 — 운영자 typo 가 실제 shell 호출로 새지 않음.
- 모든 실패 모드 (timeout / non-zero / empty / malformed / runner raise / unsupported) 가 *non-actionable* 로 surface → 컴포저가 deterministic 으로 fall-through. 라이브 tier 가 다운돼도 supervisor tick 정지 불가.
- protected branch / force push / 큐 dedup / branch lock / session marker 가드는 Round 4 그대로.

## 실제 라이브를 켜는 운영 절차 (요약)

1. 호스트에 `claude` CLI 가 설치되어 있고 인증이 끝났는지 확인 (`claude -p "ping"` 으로 비공식 검증).
2. supervisor 환경변수에 다음을 추가:
   - `YULE_CLAUDE_DECISION_PROVIDER=external,deterministic`
   - `YULE_CLAUDE_DECISION_LIVE_ENABLED=true`
   - (선택) `YULE_CLAUDE_DECISION_LIVE_BINARY=/opt/anthropic/claude` — 비표준 경로일 때만.
   - (선택) `YULE_CLAUDE_DECISION_LIVE_MODEL=claude-haiku-4-5-20251001` — 모델 핀.
   - (선택) `YULE_CLAUDE_DECISION_LIVE_TIMEOUT_SECONDS=5.0` — clamped to `[0.5, 30.0]`.
   - (선택) `YULE_CLAUDE_DECISION_LIVE_EXTRA_ARGS=--no-update,--allowedTools=none` — CLI 추가 인자.
3. 슈퍼바이저 시작 시 stdout 의 `claude decision port composed: enabled=external,deterministic fallback=deterministic live=on` 한 줄로 라이브 활성 확인.
4. record-only 와 동시에 켜고 싶으면 `YULE_CLAUDE_DECISION_PROVIDER=external,record,deterministic` + `YULE_CLAUDE_DECISION_RECORD_PATH=/var/log/yule/decision-shadow.jsonl` — external 이 actionable 응답을 주면 그 verdict 가, 아니면 record 가 캡처 후 deterministic 으로 fall-through.
5. 끄려면 `YULE_CLAUDE_DECISION_LIVE_ENABLED` 만 비우면 됨 — provider chain 은 그대로 둬도 어댑터가 None 을 반환해 트레이스에 `live=off` 로 기록.

## 본 PR 비범위 → 후속 PR 매핑 (Round 4-ter 갱신)

- `claude -p` 외 다른 라이브 클라이언트 (Anthropic SDK / 호스팅 sidecar) 활성화 → 별도 PR. 같은 `external_callable_factory` hook 한 곳만 교체.
- discussion follow-up 시 actionable advance 의 fast-path 통합 (현재는 skip 만 short-circuit) → 별도 PR.
- `next_task_selector` 자체에서 `DECISION_KIND_NEXT_TASK` 호출 → 별도 PR.
- Discord 라이브 결정 통지 / 운영자 대시보드 → 별도 PR.

## 외부 blocker

- 없음. Round 4-ter 도 hard-rail 안에서 land — 운영자가 두 env 키를 모두 명시 opt-in 하지 않는 한 deterministic-only 동작 유지.
