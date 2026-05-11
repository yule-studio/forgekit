---
title: "issue #81 통합 polish — 회귀 리포트"
kind: report
issue: 81
parent_issue: 73
session_id: issue-81-integration-polish
project: yule-studio-agent
created_at: 2026-05-11T00:00:00+09:00
status: captured
branch: feature/issue-81-integration-polish
tags: [report, issue-81, integration, regression, handoff]
contract: research-forum-export/v0
---

# 한 줄 요약

머지된 main (HEAD `4acb160`) 기준 전체 unittest 3493/3493 OK + cross-axis 통합 슈트 160/160 OK. #81 worktree split 3 축은 충돌 없이 결합되며, 100% 까지 잔여 작업 10 종을 후속 이슈로 분리.

# 회귀 결과

| 슈트 | 명령 | 결과 |
| --- | --- | --- |
| 전체 unittest discover | `PYTHONPATH=src python3 -m unittest discover -s tests -t .` | **3493 OK, skip 5, 8.13s** |
| Cross-axis 통합 슈트 (13 모듈) | `PYTHONPATH=src python3 -m unittest -v tests.job_queue.test_autonomy_producer ... tests.runtime.test_run_service_coding_executor` | **160 OK, 0.254s** |

skip 5 의 출처: 환경 의존 (Anthropic / Ollama / GitHub App live) 테스트. 본 회귀의 의도된 skip.

negative-path 로그 (예: `RuntimeError: status=401`, `claude backend exploded`, `audit pipeline down`, `unclosed event loop`) 는 *provider failure → fallback* / *audit writer raise → swallow* / *Python 3.9 asyncio ResourceWarning* 의 의도된 출력 — 회귀 차단 없음.

# Cross-axis 통합점

| 결합 | 확인된 흐름 | 회귀 슈트 |
| --- | --- | --- |
| autonomy ↔ discussion | producer tick 이 `discussion_followup.dispatch_unresolved` 를 호출, follow-up 결과를 `session.extra['discussion_followup']['by_turn']` 마커로 dedup | `test_autonomy_producer`, `test_discussion_followup`, `test_completion_funnel` |
| autonomy ↔ knowledge | retrieval evidence 는 read-only — producer 가 큐 직접 enqueue 하지 않음 (§ 16-ter.6) | `test_provider_routing`, `test_provider_availability_summary` |
| knowledge ↔ discussion | `ContextPack.relevant_knowledge` slot 가 `KnowledgeRetriever.with_signals` 결과를 받음 | `test_discussion_context_pack`, `test_retrieval` |
| 세 축 wiring | `runtime.run_service` 가 producer / executor / status / decision port 합성, supervisor watch loop 가 같은 tick 으로 호출 | `test_run_service*`, `test_supervisor_autonomy_tick`, `test_status_autonomy_surface`, `test_decision_port_run_service_wiring` |

cross-axis 충돌 없음 — 세 축은 같은 `session.extra` schema + 같은 4-state vocabulary + 같은 `RuntimeStatusReport` shape 을 공유.

# main 머지 상태 (snapshot)

머지된 PR: #75 / #76 / #77 / #78 / #79 / #80 / #82.
열린 PR: #83 (knowledge-geeknews canonical title).
PR 미생성 branch: `feature/issue-81-discussion-gateway` (`512ce7c`), `feature/issue-81-autonomy-execution` (`38e9332`).

# 마스터 플랜 § 3 완료도 수치 — 갱신

| 영역 | 이전 | 갱신 |
| --- | --- | --- |
| 운영 골격 | 65~75% | **80~85%** |
| Discord 기술 토의 능력 | 40~50% | **50~60%** |
| 완전 자율 코딩 루프 | 45~55% | **70~80%** |
| 역할별 자료 수집/정형화 루프 | 25~35% | **50~60%** |
| 실제 회사처럼 굴러가는 종합 수준 | 45~55% | **60~70%** |

근거 / 상한 유보 사유는 [[2026-05-11_issue-81-decision-integration-polish]] § D-81-3 ~ D-81-7 참조.

# Hard rails 보존

- protected branch 직접 push / force push: 가드 그대로.
- live LLM 코드 편집기: 본 worktree 비활성, F-81-4 별도.
- live Claude decision provider: 본 worktree 비활성, 두 단계 env opt-in 미설정 시 deterministic-only.
- 큐 dedup 단일 지점 원칙: producer 가 큐 직접 enqueue 하지 않음.
- secret / API key 누출: 회귀 슈트에 가드 (provider 어댑터 테스트) 그대로 통과.

# 후속 이슈 (10 종)

F-81-1 ~ F-81-10 — [[2026-05-11_issue-81-decision-integration-polish]] § "후속 이슈 드래프트" 표 그대로.

# Issue #81 progress comment 본문 (운영자가 직접 post)

```markdown
## 📈 Progress — #81 통합 polish (2026-05-11)

머지된 main 기준으로 #81 worktree split 3 축 (discussion / autonomy / knowledge) 의 cross-axis 결합을 단일 worktree (`feature/issue-81-integration-polish`) 에서 회귀 검증하고, master plan 의 완료도 수치를 갱신했습니다.

### 회귀

- 전체 unittest discover: 3493/3493 OK (skip 5), 8.13s.
- Cross-axis 통합 슈트 (autonomy / knowledge / discussion / runtime 13 모듈): 160/160 OK, 0.254s.
- 세 축은 같은 `session.extra` schema + 4-state vocabulary + `RuntimeStatusReport` shape 을 공유하며 충돌 없이 결합.

### main 머지 상태

- 머지: PR #75 #76 #77 #78 #79 #80 #82 (autonomy / knowledge 축, Round 4 / 4-bis / 4-ter / 4 마무리 시리즈).
- OPEN: PR #83 (knowledge-geeknews canonical title).
- PR 미생성: `feature/issue-81-discussion-gateway` (`512ce7c`), `feature/issue-81-autonomy-execution` (`38e9332`).

### Master plan § 3 완료도 수치 갱신

| 영역 | 이전 | 본 PR 갱신 |
| --- | --- | --- |
| 운영 골격 | 65~75% | **80~85%** |
| Discord 기술 토의 | 40~50% | **50~60%** |
| 완전 자율 코딩 루프 | 45~55% | **70~80%** |
| 자료 수집/정형화 | 25~35% | **50~60%** |
| 종합 | 45~55% | **60~70%** |

상한 유보 사유는 통합 polish PR 의 decision 노트 참조.

### 후속 이슈 (10 종)

본 PR 비범위 → 후속 이슈 분리.

1. discussion-gateway PR 생성 + 머지 (branch ready)
2. autonomy-execution PR 생성 + 머지 (branch ready)
3. knowledge-geeknews PR #83 머지
4. live LLM 코드 편집기 활성화 (운영자 승인 + cost)
5. urllib `BytesFetcher` 한 조각 (RSS/Atom/GH releases 동시 라이브)
6. sitemap / html_list / html_detail / github_api 라이브 fetcher
7. runtime service spawn `eng-research-collector`
8. `SourceRefreshState` 영속화 (sqlite or vault sidecar)
9. `next_task_selector` 의 `DECISION_KIND_NEXT_TASK` 호출
10. Discord escalation alert (blocked N 분 지속)

상세는 본 PR 의 task-log / decision 노트 참조.
```

# 운영자 다음 액션

1. 본 PR (`feature/issue-81-integration-polish`) 검토 + 머지.
2. 위 progress comment 본문을 issue #81 에 post (운영자 직접).
3. F-81-1 / F-81-2 / F-81-3 부터 PR 생성 (회귀 / 머지 순서는 decision § D-81-8 권장).
4. F-81-4 ~ F-81-10 은 별도 worktree + 새 이슈로 분리.

# 관련 문서

- [[2026-05-11_issue-81-task-log-integration-polish]]
- [[2026-05-11_issue-81-decision-integration-polish]]
- [[2026-05-11_issue-81-research-integration-polish]]
- [[2026-05-09_issue-73-task-log-tech-lead-runtime-loop]]
