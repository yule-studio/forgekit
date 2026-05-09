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
