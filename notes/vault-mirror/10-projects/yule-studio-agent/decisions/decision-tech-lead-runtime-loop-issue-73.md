---
title: "tech-lead runtime loop — 12 결정 (D-73-1 ~ D-73-12)"
kind: decision
issue: 73
parent_issue: 20
session_id: issue-73-tech-lead-runtime
project: yule-studio-agent
created_at: 2026-05-09T00:00:00+09:00
status: decided
tags: [decision, tech-lead-runtime, foundation]
---

# 목표

tech-lead runtime loop 의 4 단계 (coding executor / completion hook + selector / decision layer / 검증) 를 본 PR 에서 *foundation* 단계까지 land. 12 결정으로 고정.

# 결정

## A. Coding executor worker (Phase 1)

| ID | 결정 |
| --- | --- |
| **D-73-1** | 새 `job_type = "coding_execute"` 신설. payload = `CodingExecuteRequest` (session_id, executor_role, write_scope, forbidden_scope, base_branch, branch_hint, generated_prompt, dry_run). |
| **D-73-2** | `CodingExecutorWorker.process_job` 가 7 단계 (worktree → edit → test → commit → push → draft PR → state transition) 를 *Protocol 분리* 형태로 호출. 본 PR 은 Protocol scaffolds + dry_run path 까지. live executor wiring 은 후속 PR. |
| **D-73-3** | force push / protected branch (`main` / `master` / `dev` / `prod` / `release`) 직접 push 는 worker 차원에서 **하드 차단**. `is_protected_branch` 가 True 면 `FAILED_TERMINAL` + reason="protected_branch_blocked". |
| **D-73-4** | 새 `ServiceKind.CODING_EXECUTOR` 추가. service id = `eng-coding-executor`. service registry 에 등록 — `runtime up` 자동 spawn 은 후속 PR 에서 사용자 승인 후 설정 (본 PR spec 만). |

## B. Completion hook + next-task selector (Phase 2)

| ID | 결정 |
| --- | --- |
| **D-73-5** | 작업 종료 4 표준 상태: `done` / `blocked` / `needs_approval` / `retry_ready`. `JobCompletionEvent` 데이터 모델로 캡슐화. agent_ops_audit 자동 기록. |
| **D-73-6** | next-task 우선순위 (deterministic, deterministic): (1) CI 실패 PR → re-plan, (2) 승인 coding_job=ready → coding_execute, (3) 미해결 discussion thread, (4) orphan open issue. selector 는 *순수 함수* — GitHub / session state 는 Protocol injection. |
| **D-73-7** | selector 결과 + 사유는 `session.extra.next_task_selection` 에 영속. 다음 enqueue 가 발생하면 `next_task_dispatched_at` 도 stamp. |
| **D-73-8** | `blocked` 상태는 자동 재시도 X. 운영자 알림 (gateway-mediated comment / `#봇-상태` 알림) 만. 본 PR 은 hook 정의까지, Discord 통지 wiring 은 후속. |

## C. Claude decision layer (Phase 3)

| ID | 결정 |
| --- | --- |
| **D-73-9** | 4 mode: `discussion` / `research_only` / `implementation_candidate` / `clarification_needed`. `DecisionResult` 가 mode + confidence + reason + context_pack_id 보존. |
| **D-73-10** | deterministic fast-path 우선. fast-path 에서 mode 가 결정되지 않을 때만 classifier 호출 (Protocol). classifier 는 본 PR 에서 fake 만 land — 실 LLM 은 후속 PR. |
| **D-73-11** | `ContextPack` 데이터 모델 = id / related_notes / recent_threads / related_issues / related_prs / code_hints / created_at. 빌더는 *순수 함수* — retrieval / GitHub query 는 Protocol injection. |

## D. 검증 + 운영 (Phase 4)

| ID | 결정 |
| --- | --- |
| **D-73-12** | 5 commit 분할 (Obsidian / Phase 1 / Phase 2 / Phase 3 / runtime registry 통합 + 정책 회귀 test). 모든 신규 모듈은 단위 테스트 동봉. PR body = `.github/PULL_REQUEST_TEMPLATE` 4 섹션 + Audit append. governance 정책 (#69) 엄격 준수. |

# 본 PR 비범위 (후속 PR 분리)

| 항목 | 후속 |
| --- | --- |
| `coding_execute` worker 의 live executor 호출 (claude / codex 등) | 별도 PR — 사용자 승인 + secret 확인 후 |
| `runtime up` 의 새 서비스 자동 spawn | 별도 PR — production rollout 정책 |
| Discord `#봇-상태` 알림 wiring (`blocked` 통지) | 별도 PR |
| 실 GitHub state query (CI 결과 / open PR / open issue) | G6 LiveGithubAppClient 확장 PR |
| 실 Claude classifier 호출 | 사용자 승인 + API key 확인 후 별도 PR |
| autonomy_policy hook 통합 (M10d) | 후속 |

# 검증

`tests/agents/test_*` 5 종 + 기존 회귀 0. 모든 worker / selector / router / context_pack 가 fake injection 으로 단위 테스트 가능.

## 관련 문서

- [[CLAUDE]]
- [[research-engineering-agent-governance-synthesis-issue-69]]
- [[decision-engineering-agent-authoring-policy-issue-69]]
- [[research-tech-lead-runtime-loop-issue-73]]
- [[task-log-tech-lead-runtime-loop-issue-73]]
