---
title: "issue #81 통합 polish — 사전 조사"
kind: research
issue: 81
parent_issue: 73
session_id: issue-81-integration-polish
project: yule-studio-agent
created_at: 2026-05-11T00:00:00+09:00
status: captured
branch: feature/issue-81-integration-polish
tags: [research, issue-81, integration, regression]
contract: research-forum-export/v0
---

# 배경

#81 (엔지니어 에이전트 팀 단위 자동화) 은 worktree split plan 에 따라 세 갈래로 갈라졌다.

- `feature/issue-81-discussion-gateway` — gateway / tech-lead 경계, Discord 기술 토의 surface 가독성, discussion_mode operator surface.
- `feature/issue-81-autonomy-execution` — next-task auto handoff, completion funnel, coding executor live path, CI failure → retry/blocked/approval 강화.
- `feature/issue-81-knowledge-geeknews` — 역할별 상시 자료 수집, request-time retrieval, canonical title / share boundary / Obsidian knowledge contract.

세 축이 main 위에서 *동시에* 굴러갈 때 (a) cross-axis 충돌 / (b) 운영자 surface 일관성 / (c) handoff 누락 이 없는지 한 곳에서 검증하는 게 본 worktree 의 목적이다.

# 통합 시점 main 의 진실 (2026-05-11)

| 머지 영역 | 핵심 모듈 | 회귀 진입점 |
| --- | --- | --- |
| autonomy 축 (Round 4 / 4-bis / 4-ter / 4 마무리) | `agents/job_queue/{autonomy_producer,autonomy_lock,discussion_followup,completion_funnel,claude_decision_seam,claude_subprocess_adapter}.py` + `runtime/run_service.py` + `runtime/status.py` | `tests.job_queue.test_autonomy_producer`, `test_discussion_followup`, `test_completion_funnel`, `test_decision_port_run_service_wiring`, `tests.runtime.test_supervisor_autonomy_tick`, `test_status_autonomy_surface` |
| knowledge 축 (Round 4 시리즈) | `agents/engineering_intelligence/{provider_registry,provider_routing,providers,retrieval,feed_parser,scheduler,source_registry,role_feed_digest,share_scope}.py` | `tests.engineering_intelligence.*` |
| discussion seam | `agents/discussion/context_pack.py`, `agents/job_queue/discussion_followup.py` | `tests.engineering.test_discussion_context_pack`, `tests.job_queue.test_discussion_followup` |
| executor / CI | `agents/job_queue/{coding_executor_worker,coding_executor_live,coding_execute_dispatcher,coding_execute_progress,ci_retry_orchestrator,ci_status}.py` | `tests.agents.test_coding_executor_*`, `tests.job_queue.test_coding_execute_*`, `tests.job_queue.test_ci_retry_orchestrator`, `tests.agents.test_ci_status` |

미머지 (branch 만 존재):

- `feature/issue-81-discussion-gateway` (commit `512ce7c`) — Discord 토의 surface 가독성 향상 + gateway/tech-lead 경계 정렬.
- `feature/issue-81-autonomy-execution` (commit `38e9332`) — 역할별 반복 실수 ledger + preflight seam round 1.
- `feature/issue-81-knowledge-geeknews` (commit `baa1c93`, PR #83) — engineering-knowledge 수집 노트 visible title GeekNews 스타일.

# 세 축이 결합하는 단일 신호 표면

세 축이 동시에 운영자에게 도달하는 한 화면은 `runtime.status.RuntimeStatusReport` + `runtime.status_poster.post_runtime_status_summary` 두 함수의 결과다 (Round 4 마무리, 마스터 플랜 § 16-ter / Round 4 후속 / Round 4 마무리). 본 회귀에서 검증한 결합은 아래와 같다.

1. **autonomy 가 만든 dispatch** → `RuntimeAutonomyJournal` ring buffer (16 entry) → `RuntimeStatusReport.autonomy_recent` → `render_runtime_status_text` / Discord post / JSON.
2. **completion funnel decision** → `session.extra['completion_funnel']['history']` 32 cap → `collect_recent_completion_funnel(session_lister=...)` → `RuntimeStatusReport.completion_funnel_recent` → 같은 surface.
3. **knowledge retrieval evidence** → `KnowledgeRetriever.with_signals` → `discussion.context_pack.ContextPack.relevant_knowledge` slot → discussion follow-up 의 `DecisionRequest.facts` 또는 prompt context.
4. **decision seam 의 verdict** → `consult_decision_port(port, request) -> (DecisionResponse, DecisionInvocationTrace)` → `AutonomyDispatch.payload['decision_invocation']` → status surface 의 `errored/locked/dispatched` 카운터.

이 흐름이 cross-axis 회귀 (160 케이스) 에서 한 번에 통과했다는 것은, 세 축이 *같은 session.extra schema* + *같은 4-state vocabulary* + *같은 RuntimeStatusReport shape* 을 공유함을 의미한다.

# 잔여 회귀 위험 식별

cross-axis 통과는 했지만 머지 안 된 #81 분기들이 추가로 들어올 때 다시 봐야 할 지점.

| 위험 | 어디서 | 대응 |
| --- | --- | --- |
| discussion-gateway 가 Discord 채널 router 의 정책 텍스트를 바꾸면 `tests.discord.*` 변경 필요 | `src/yule_orchestrator/discord/*`, `tests/discord/*` | gateway PR 안에서 회귀 같이 land |
| autonomy-execution 의 ledger / preflight seam 이 producer 에 새 sub-producer 를 추가하면 `AutonomyProducer.tick()` payload schema 변경 가능 | `agents/job_queue/autonomy_producer.py` | producer 직접 enqueue 금지 원칙 (마스터 플랜 § 16-ter.6) 유지 — 새 dispatcher 한 곳만 추가 |
| knowledge-geeknews canonical title 이 frontmatter `topic` 키와 정렬되지 않으면 인덱서 회귀 | `agents/engineering_intelligence/obsidian*`, `tests.engineering_intelligence.test_obsidian` | PR #83 의 48 cases 회귀로 가드. 본 회귀에서도 통과 확인 |
| live LLM editor 활성화 시 `coding_executor_live` 의 `RecordOnlyCodeEditor` 교체로 verdict 가 actionable 로 surface | `coding_executor_live.py`, `coding_executor_worker.py` | 본 worktree 비범위. 운영자 승인 + 별도 PR 게이트 필요 (Round 2 매핑 그대로) |

# 운영자 handoff 요구

- 운영자가 `yule runtime status` / Discord `#봇-상태` / Obsidian task-log / GitHub PR 댓글 어디에서 봐도 4-state + 9 종 operator action 이 같은 라벨로 보여야 한다 (Round 4 마무리 land 완료).
- 세 축이 머지될 때마다 마스터 플랜 § 3 수치 / § 16-ter / § 16-quater 를 한 번씩 갱신해야 한다. 본 PR 은 § 3 만 갱신.
- live LLM editor / live decision provider 두 개는 *반드시* 운영자 명시 opt-in 환경변수 양쪽이 켜져 있을 때만 surface — 두 단계 opt-in 정책 (마스터 플랜 § 16-quater.2 / § 16-bis.5) 그대로.

# 본 PR 의 결정 — [[2026-05-11_issue-81-decision-integration-polish]]

회귀 범위 / 수치 갱신 폭 / 후속 이슈 분리 기준은 본 노트 옆 decision 노트에서 확정한다.

# 관련 문서

- [[2026-05-11_issue-81-task-log-integration-polish]]
- [[2026-05-11_issue-81-decision-integration-polish]]
- [[2026-05-09_issue-73-research-tech-lead-runtime-loop]]
- [[2026-05-09_issue-73-decision-tech-lead-runtime-loop]]
- [[2026-05-09_issue-73-task-log-tech-lead-runtime-loop]]
