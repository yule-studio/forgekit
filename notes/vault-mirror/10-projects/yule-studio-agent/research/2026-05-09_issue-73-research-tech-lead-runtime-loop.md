---
title: "tech-lead runtime loop — coding executor + next-task + decision layer 분석"
kind: research
issue: 73
parent_issue: 20
session_id: issue-73-tech-lead-runtime
project: yule-studio-agent
created_at: 2026-05-09T00:00:00+09:00
sources:
  - https://github.com/yule-studio/yule-studio-agent/issues/73
  - https://github.com/yule-studio/yule-studio-agent/issues/20
  - https://github.com/yule-studio/yule-studio-agent/issues/69
  - src/yule_orchestrator/runtime/services.py
  - src/yule_orchestrator/agents/job_queue/state_machine.py
  - src/yule_orchestrator/agents/coding/job.py
  - src/yule_orchestrator/agents/coding/authorization.py
tags: [research, tech-lead-runtime, coding-executor, next-task]
---

# 목표

Yule 을 *작업 완료 후 다음 작업을 스스로 선택하고 이어가는 tech-lead runtime* 으로 확장. 4 단계 (coding executor / completion hook + selector / decision layer / 검증) 를 본 PR 한 묶음에서 foundation 까지 land.

# 현재 Yule 기준선

| 영역 | 위치 | 상태 |
| --- | --- | --- |
| Job queue + 11 state | `agents/job_queue/{store,state_machine}.py` | ✅ 운영 |
| 4 worker (research / role / approval / obsidian) | `agents/job_queue/{research,role_take,approval,obsidian_writer}_worker.py` | ✅ 운영 |
| `coding_job` 데이터 모델 + 6 status | `agents/coding/job.py` | ✅ contract 정의, *executor worker 없음* |
| 7 role authorization | `agents/coding/authorization.py` | ✅ |
| Service registry + spec | `runtime/services.py` (engineering profile) | ✅ |
| Standalone runner (`yule run-service`) | `runtime/run_service.py` | ✅ |
| Autonomy / agent_ops_audit (#69) | `agents/lifecycle/{autonomy_policy,agent_ops_log}.py` | ✅ |
| GitHub WorkOS G1~G6 (#25 / #69) | `agents/github_workos/`, `github_app/` | ✅ |
| Governance (#69) | `policies/runtime/agents/engineering-agent/governance.md` | ✅ 본 PR 의 정책 출처 |

비어 있는 것:

- `coding_job=ready` 를 소비할 *executor worker* — `coding/job.py` 의 STATUS_READY 가 dead-end.
- *next-task selector* — 작업 종료 후 다음을 자동 선택하는 layer.
- *decision layer* — Discord 입력의 4 모드 분류 (discussion / research_only / implementation_candidate / clarification_needed).
- *Context Pack* — 관련 note / thread / issue·PR / code hint 묶음.

# 4 단계 설계

## Phase 1 — coding executor worker

```
Job(job_type=coding_execute) ←── enqueue when coding_job.status == ready
  payload:
    session_id, executor_role, write_scope, base_branch, branch_hint, dry_run
  process_job:
    1. CodingExecuteRequest.from_payload
    2. WorktreeProvisioner.provision(branch_hint, base_branch)
    3. CodeEditor.apply(generated_prompt, write_scope, forbidden_scope)
    4. TestRunner.run(scope=changed files)
    5. Committer.commit(message, author=executor_role bot)
    6. Pusher.push(branch)         ← protected branch / force push 거부
    7. DraftPRCreator.open(repo, head, base, body)  ← repo PR template 적용
    8. transition: SAVED 또는 FAILED_RETRYABLE
```

각 단계는 Protocol 으로 분리해 fake 주입 + 실 wiring 후속 PR.

새 service kind: `ServiceKind.CODING_EXECUTOR`, spec id `eng-coding-executor`.

## Phase 2 — completion hook + next-task selector

작업 종료 시 4 표준 상태:

| status | 의미 |
| --- | --- |
| `done` | 정상 완료 — 다음 task 자동 enqueue |
| `blocked` | 외부 blocker (secret / approval 부재 / 외부 시스템 장애) — 운영자 알림, 자동 재시도 X |
| `needs_approval` | L3+ 승인 필요 — `#승인-대기` 카드 게시 |
| `retry_ready` | 자동 재시도 가능 (CI 실패 / 일시 네트워크) — 백오프 후 재진입 |

next-task 우선순위 (deterministic):

1. CI 실패한 활성 PR → re-plan / retry job enqueue
2. 승인된 coding_job=ready → coding_execute job enqueue
3. 결론 없는 discussion thread → role_take 또는 research_collect 추가
4. 세션 미연결 open issue → intake

selector 결과는 `session.extra.next_task_selection` 에 영속, agent_ops_audit 에 행 추가.

## Phase 3 — Claude decision layer

deterministic fast-path 우선:

- `[Research]` prefix / "조사해줘" / "리서치만" → `research_only`
- "구현해줘" / "PR 올려줘" / "코드 수정" → `implementation_candidate`
- 명시적 질문 / 결정 요청 ("어떻게 할까?" / "결정해줘") → `discussion`
- 모호 → classifier 호출 → `clarification_needed` 또는 위 3 중 하나

classifier 는 Protocol — fake / claude / ollama 어디서든 plug 가능.

Context Pack 구조:

```
ContextPack(
  id: str,
  related_notes: tuple[str, ...],     # vault path
  recent_threads: tuple[str, ...],
  related_issues: tuple[int, ...],
  related_prs: tuple[int, ...],
  code_hints: tuple[str, ...],         # repo path
  created_at: ISO,
)
```

# 흡수 / 보류 / 비도입

| 결정 | 본 PR | 후속 |
| --- | --- | --- |
| 새 job_type `coding_execute` + 7 protocol scaffold | ✅ | runtime live wiring (worktree shell / 실 LLM 호출 / 실 push) |
| 새 service kind `CODING_EXECUTOR` + service spec | ✅ | `runtime up` 의 spawn 순서 통합 |
| completion hook 4 표준 상태 | ✅ | 기존 4 worker 의 hook 합류 wiring |
| next-task selector 우선순위 4 단계 | ✅ | 실제 GitHub state / session state query 인터페이스 |
| decision layer 4 모드 + deterministic fast-path | ✅ | 실제 Claude classifier 호출 |
| Context Pack 데이터 모델 + 빌더 | ✅ | retrieval / memory 통합 |
| `yule runtime up` 에 새 서비스 spawn | ⏳ 후속 | 본 PR 은 spec 까지만 |
| 실 GitHub App push (G6 LiveGithubAppClient) wiring | ⏳ 후속 | 본 PR 은 Protocol 까지 |
| autonomy_policy hook 통합 | ⏳ M10d 후속 | — |

# 왜 회사형 시니어 팀 운영에 필요한가

본 layer 가 land 되면 부서가 *작업 완료 → 다음 작업 자동 선택* 의 루프를 갖는다. 시니어 PM/EM 이 매번 "다음 뭐해?" 라고 묻지 않아도, 부서 자체가 우선순위 표를 갖고 deterministic 하게 선택한다. 외부 blocker 만 사람을 부르고, 그 외에는 계속 흘러간다.

# 구현 위치

| 산출물 | 위치 |
| --- | --- |
| Coding executor worker | `src/yule_orchestrator/agents/job_queue/coding_executor_worker.py` |
| Completion hook | `src/yule_orchestrator/agents/job_queue/completion_hook.py` |
| Next-task selector | `src/yule_orchestrator/agents/job_queue/next_task_selector.py` |
| Decision router | `src/yule_orchestrator/agents/decision/__init__.py`, `router.py` |
| Context Pack | `src/yule_orchestrator/agents/decision/context_pack.py` |
| Service spec 갱신 | `src/yule_orchestrator/runtime/services.py` |
| Tests | `tests/agents/test_coding_executor_worker.py`, `test_completion_hook.py`, `test_next_task_selector.py`, `test_decision_router.py`, `test_context_pack.py` |
| Obsidian mirror | `notes/vault-mirror/10-projects/yule-studio-agent/{research,decisions,task-logs}/2026-05-09_issue-73-*.md` |

# 리스크 + 다음 액션

리스크:

- *foundation only* — 본 PR 의 worker 는 실 작업을 하지 않는다 (Protocol 까지). live wiring 은 후속 PR 에서 사용자 승인 + secret 확인 후. 정책 위반 / 회귀 위험 없음.
- 새 service kind 가 `runtime up` 에 spawn 되면 standalone runner 가 호출됨 — 본 PR 의 spec 은 *registered but not spawned by default* 형태로 land.
- decision layer 의 fast-path keyword 가 `role_profiles.activation_keywords` 와 충돌 가능 — fast-path 는 명시적 모드 결정이라 우선, 충돌 발견 시 keyword 우선순위 표를 후속 PR 에서.

다음 액션 (본 PR):

1. Phase 1 land — coding_executor_worker + scaffold + tests (commit 2)
2. Phase 2 land — completion_hook + next_task_selector + tests (commit 3)
3. Phase 3 land — decision/router + context_pack + tests (commit 4)
4. Phase 4 land — service registry 통합 + 회귀 test + Obsidian backlink (commit 5)
5. push + draft PR + progress comment

## 관련 문서

- [[CLAUDE]]
- [[2026-05-08_issue-69-research-engineering-agent-governance-synthesis]]
- [[2026-05-08_issue-69-decision-engineering-agent-authoring-policy]]
- [[2026-05-09_issue-73-decision-tech-lead-runtime-loop]]
- [[2026-05-09_issue-73-task-log-tech-lead-runtime-loop]]
